"""Durable, exact full-refund orchestration with no fulfillment side effects."""
import json

from integrations.stripe import StripeIntegrationError


class RefundError(ValueError):
    pass


class RefundService:
    def __init__(self, db, stripe):
        self.db, self.stripe = db, stripe

    @staticmethod
    def _verify(order, refund):
        expected=(order["stripe_payment_intent_id"],order["captured_amount"],order["captured_currency"].upper())
        actual=(refund.get("payment_intent"),refund.get("amount"),str(refund.get("currency") or "").upper())
        if actual != expected:
            raise StripeIntegrationError("refund_mismatch","Refund details require manual review",ambiguous=True,
                                         provider_id=refund.get("id"))
        if refund.get("status") not in ("succeeded","pending","failed"):
            raise StripeIntegrationError("refund_status_unknown","Refund status requires manual review",ambiguous=True,
                                         provider_id=refund.get("id"))

    def create_full(self, public_id, actor="seller"):
        order=self.db.get_order(public_id)
        if not order: raise RefundError("Order not found")
        if order["refund_state"]=="refunded": return order
        if order["refund_state"]=="manual_resolution": raise RefundError("Refund requires reconciliation")
        if order["state"]!="sold" or order["payment_state"]!="captured": raise RefundError("Only a sold, captured order can be refunded")
        if order["captured_amount"] is None or not order["captured_currency"]: raise RefundError("Persisted capture amount is unavailable")
        claim=self.db.claim_operation(public_id,"stripe_full_refund")
        if claim["disposition"] in ("busy","already_complete"): return self.db.get_order(public_id)
        if claim["disposition"]!="claimed": raise RefundError("Refund cannot be created")
        metadata={"fulfillment_state_at_request":order["fulfillment_state"],"amount":order["captured_amount"],
                  "currency":order["captured_currency"].upper()}
        pending=self.db.compare_and_set_refund(public_id,"none","pending",actor=actor,metadata=metadata,
                                               dedupe_key=f"refund-request:{public_id}")
        if not pending: return self.db.get_order(public_id)
        try:
            refund=self.stripe.create_full_refund(order["stripe_payment_intent_id"],order["captured_amount"],
                                                   order["captured_currency"],public_id)
            self._verify(order,refund)
            return self._record(order,refund,claim["token"],actor)
        except StripeIntegrationError as exc:
            diagnostic=json.dumps(exc.safe_dict(),sort_keys=True)
            if exc.ambiguous:
                self.db.complete_operation(public_id,"stripe_full_refund",claim["token"],diagnostic=diagnostic,manual=True)
                return self.db.compare_and_set_refund(public_id,"pending","manual_resolution",actor=actor,
                    metadata={"kind":exc.kind},stripe_refund_id=exc.provider_id) or self.db.get_order(public_id)
            self.db.fail_operation(public_id,"stripe_full_refund",claim["token"],diagnostic)
            return self.db.compare_and_set_refund(public_id,"pending","none",actor=actor,
                metadata={"kind":exc.kind}) or self.db.get_order(public_id)

    def _record(self, order, refund, token=None, actor="seller"):
        state=refund["status"]
        if state=="failed":
            if token: self.db.fail_operation(order["public_id"],"stripe_full_refund",token,"confirmed provider failure")
            target="none"
        else:
            if token: self.db.complete_operation(order["public_id"],"stripe_full_refund",token,result=state)
            target="refunded" if state=="succeeded" else "pending"
        current=self.db.get_order(order["public_id"])["refund_state"]
        return self.db.compare_and_set_refund(order["public_id"],current,target,actor=actor,
            metadata={"provider_status":state},stripe_refund_id=refund["id"]) or self.db.get_order(order["public_id"])

    def recheck(self, public_id, actor="seller"):
        order=self.db.get_order(public_id)
        if not order or order["refund_state"] not in ("pending","manual_resolution"):
            raise RefundError("Refund cannot be rechecked")
        if not order.get("stripe_refund_id"): raise RefundError("No known Refund ID is available")
        try:
            refund=self.stripe.retrieve_refund(order["stripe_refund_id"])
            self._verify(order,refund)
            return self._record(order,refund,actor=actor)
        except StripeIntegrationError as exc:
            if exc.ambiguous: return self.db.compare_and_set_refund(public_id,order["refund_state"],"manual_resolution",actor=actor,
                metadata={"kind":exc.kind}) or self.db.get_order(public_id)
            self.db.reconcile_operation_failure(public_id,"stripe_full_refund",json.dumps(exc.safe_dict(),sort_keys=True))
            return self.db.compare_and_set_refund(public_id,order["refund_state"],"none",actor=actor,
                metadata={"kind":exc.kind}) or self.db.get_order(public_id)
