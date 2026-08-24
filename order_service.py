"""Retry-safe storefront order orchestration.

All provider calls happen between short Database method calls; this module never
holds a SQLite connection while talking to Stripe or eBay.
"""
import json

from integrations.ebay import EbayError
from integrations.stripe import StripeIntegrationError


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class OrderService:
    def __init__(self, db, stripe, ebay_resolver, config):
        self.db, self.stripe, self.ebay_resolver, self.cfg = db, stripe, ebay_resolver, config

    def create_checkout(self, sku, prior_id=None):
        prior = self.db.get_order(prior_id) if prior_id else None
        if prior and prior["state"] in ("checkout_pending", "authorization_pending") and prior.get("stripe_checkout_session_id"):
            if self.cfg.stripe_fake_mode:
                return prior, None
            remote = self.stripe.retrieve_session(prior["stripe_checkout_session_id"])
            return prior, _value(remote, "url")
        order = self.db.create_storefront_order(sku, self.cfg.reservation_seconds)
        item = self.db.get_item(order["item_id"])
        try:
            remote = self.stripe.create_checkout(
                order, item, self.cfg.stripe_success_url.replace("{ORDER_ID}", order["public_id"]),
                self.cfg.stripe_cancel_url.replace("{ORDER_ID}", order["public_id"]))
            order = self.db.attach_checkout(order["public_id"], _value(remote, "id"),
                                            _value(remote, "payment_intent"), source="shopper")
            return order, _value(remote, "url")
        except StripeIntegrationError as exc:
            if exc.ambiguous:
                self.db.transition_order(order["public_id"], "manual_resolution", error=json.dumps(exc.safe_dict()), source="shopper", category="checkout_ambiguous")
            else:
                self.db.release_order(order["public_id"], "cancelled", "checkout creation failed", source="shopper")
            raise

    def reconcile_session(self, order, session_obj=None):
        session_obj = session_obj or self.stripe.retrieve_session(order["stripe_checkout_session_id"])
        pi = _value(session_obj, "payment_intent")
        pi_id = _value(pi, "id") if pi and not isinstance(pi, str) else pi
        if pi_id:
            order = self.db.attach_payment_intent(order["public_id"], pi_id, self._fulfillment(session_obj))
        return order, pi

    @staticmethod
    def _fulfillment(session_obj):
        details = _value(session_obj, "customer_details", {}) or {}
        shipping = _value(session_obj, "shipping_details", None) or _value(session_obj, "collected_information", {}) or {}
        if _value(shipping, "shipping_details"):
            shipping = _value(shipping, "shipping_details")
        address = _value(shipping, "address", {}) or _value(details, "address", {}) or {}
        return {"customer_email": _value(details, "email") or _value(session_obj, "customer_email"),
                "shipping_name": _value(shipping, "name") or _value(details, "name"),
                "shipping_line1": _value(address, "line1"), "shipping_line2": _value(address, "line2"),
                "shipping_city": _value(address, "city"), "shipping_state": _value(address, "state"),
                "shipping_postal_code": _value(address, "postal_code"), "shipping_country": _value(address, "country"),
                "shipping_phone": _value(shipping, "phone")}

    def process_authorization(self, order, session_obj=None, payment_intent=None, source="stripe_webhook"):
        if order["state"] in ("sold", "cancelled", "expired", "unavailable", "manual_resolution"):
            return order
        if not order.get("stripe_payment_intent_id") or payment_intent is None:
            order, expanded = self.reconcile_session(order, session_obj)
            payment_intent = expanded
        if isinstance(payment_intent, str) or payment_intent is None:
            session_obj = self.stripe.retrieve_session(order["stripe_checkout_session_id"])
            order, payment_intent = self.reconcile_session(order, session_obj)
        status = _value(payment_intent, "status")
        capture_method = _value(payment_intent, "capture_method")
        if status != "requires_capture" or capture_method != "manual":
            return order  # premature/irrelevant delivery is safely recorded by webhook dedupe
        if order["state"] in ("checkout_pending", "authorization_pending"):
            order = self.db.transition_order(order["public_id"], "authorized", payment_state="authorized", source=source, category="authorization_verified")
        item = self.db.get_item(order["item_id"])
        if item["status"] == "Sold" or item["quantity"] < 1:
            self.db.transition_order(order["public_id"], "authorization_cancel_pending", conflict="Inventory sold by another channel", source=source, category="inventory_conflict")
            return self._cancel_and_release(order["public_id"], "unavailable", "inventory sold elsewhere", source)
        if order["state"] == "authorized":
            order = self.db.transition_order(order["public_id"], "ebay_withdrawal_pending", source=source, category="withdrawal_started")
        try:
            if item.get("ebay_publication_state") == "published":
                self.ebay_resolver(item)[0].delist(item)
            order = self.db.transition_order(order["public_id"], "capture_pending", source="ebay_reconciliation", category="withdrawal_confirmed")
        except EbayError as exc:
            self.db.transition_order(order["public_id"], "authorization_cancel_pending", error=json.dumps(exc.safe_dict()), source="ebay_reconciliation", category="withdrawal_failed")
            return self._cancel_and_release(order["public_id"], "cancelled", "eBay withdrawal failed", source)
        try:
            result = self.stripe.capture(order["stripe_payment_intent_id"], f"capture:{order['public_id']}")
            if _value(result, "status") != "succeeded":
                raise StripeIntegrationError("capture_ambiguous", "Capture requires review", ambiguous=True)
            self.db.transition_order(order["public_id"], "paid", payment_state="captured", source=source, category="capture_confirmed")
            return self.db.finalize_storefront_sale(order["public_id"], source=source)
        except StripeIntegrationError as exc:
            return self.db.transition_order(order["public_id"], "manual_resolution", error=json.dumps(exc.safe_dict()), source=source, category="capture_ambiguous")

    def _cancel_and_release(self, public_id, terminal, reason, source):
        order = self.db.get_order(public_id)
        if not order.get("stripe_payment_intent_id"):
            return self.db.transition_order(public_id, "manual_resolution", error='{"kind":"missing_payment_intent","message":"Cancellation requires review","ambiguous":true}', source=source, category="cancellation_ambiguous")
        try:
            result = self.stripe.cancel(order["stripe_payment_intent_id"], f"cancel:{public_id}")
            if _value(result, "status") not in ("canceled", "cancelled"):
                raise StripeIntegrationError("cancellation_ambiguous", "Cancellation requires review", ambiguous=True)
            return self.db.release_order(public_id, terminal, reason, source=source)
        except StripeIntegrationError as exc:
            return self.db.transition_order(public_id, "manual_resolution", error=json.dumps(exc.safe_dict()), source=source, category="cancellation_ambiguous")

    def seller_cancel(self, public_id):
        order = self.db.get_order(public_id)
        if not order or order["state"] not in ("checkout_pending", "authorization_pending", "authorized", "authorization_cancel_pending"):
            raise ValueError("Order cannot be cancelled")
        if not order.get("stripe_payment_intent_id") and order.get("stripe_checkout_session_id"):
            order, _ = self.reconcile_session(order)
        if order.get("stripe_payment_intent_id"):
            if order["state"] == "authorized":
                order = self.db.transition_order(public_id, "authorization_cancel_pending", source="seller", category="cancellation_requested")
            return self._cancel_and_release(public_id, "cancelled", "cancelled by seller", "seller")
        return self.db.release_order(public_id, "cancelled", "cancelled by seller", source="seller")

    def retry(self, public_id):
        order = self.db.get_order(public_id)
        if not order or order["state"] == "manual_resolution":
            raise ValueError("Order cannot be retried")
        if order["state"] == "authorization_cancel_pending":
            return self._cancel_and_release(public_id, "cancelled", "retry confirmed cancellation", "seller")
        if order["state"] in ("authorization_pending", "authorized", "ebay_withdrawal_pending", "capture_pending"):
            return self.process_authorization(order, source="seller")
        raise ValueError("Order cannot be retried")
