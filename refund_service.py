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
        amount=refund.get("amount"); currency=refund.get("currency")
        expected_currency=str(order.get("captured_currency") or "").upper()
        expected=(order.get("stripe_payment_intent_id"),order.get("captured_amount"),expected_currency)
        actual=(refund.get("payment_intent"),amount,str(currency or "").upper())
        if (not expected[0] or not isinstance(expected[1],int) or isinstance(expected[1],bool) or not expected[2] or
                not isinstance(amount,int) or isinstance(amount,bool) or not isinstance(currency,str)):
            raise StripeIntegrationError("refund_mismatch","Refund details require manual review",ambiguous=True,
                                         provider_id=refund.get("id"))
        if actual != expected:
            raise StripeIntegrationError("refund_mismatch","Refund details require manual review",ambiguous=True,
                                         provider_id=refund.get("id"))
        if refund.get("status") not in ("succeeded","pending","failed"):
            raise StripeIntegrationError("refund_status_unknown","Refund status requires manual review",ambiguous=True,
                                         provider_id=refund.get("id"))

    @staticmethod
    def normalize_webhook(event_type, obj):
        """Return the refund-shaped records carried by supported Stripe events.

        Stripe 12 refund events carry a Refund, while ``charge.refunded`` carries
        a Charge and (when useful to us) its concrete Refund records.  A charge's
        aggregate ``amount_refunded`` is deliberately not treated as one refund:
        it cannot identify which refund should be reconciled.
        """
        def scalar(value):
            return value.get("id") if isinstance(value, dict) else value
        def refund(value, fallback_pi=None, fallback_currency=None):
            if not isinstance(value, dict): return None
            metadata=value.get("metadata") if isinstance(value.get("metadata"),dict) else {}
            result={"id":scalar(value.get("id")),"payment_intent":scalar(value.get("payment_intent")) or fallback_pi,
                    "amount":value.get("amount"),"currency":value.get("currency") or fallback_currency,
                    "status":value.get("status"),"reference":metadata.get("order")}
            return result if result["id"] else None
        if event_type in {"refund.created","refund.updated","refund.failed"}:
            item=refund(obj)
            if item and event_type=="refund.failed": item["status"]="failed"
            return [item] if item else []
        if event_type=="charge.refunded":
            pi=scalar(obj.get("payment_intent")); currency=obj.get("currency")
            refunds=obj.get("refunds") or {}; values=refunds.get("data") if isinstance(refunds,dict) else None
            return [item for item in (refund(value,pi,currency) for value in (values or [])) if item]
        return []

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

    def _attention(self, order, refund, kind, dedupe_key):
        self.db.create_attention_case(order["public_id"],"refund_contradiction",
            "Stripe refund data conflicts with the recorded full refund; review required.",
            dedupe_key=f"refund-attention:{dedupe_key}:{kind}",actor="stripe_webhook")

    def _record(self, order, refund, token=None, actor="seller", dedupe_key=None):
        state=refund["status"]
        if state=="failed":
            if token: self.db.fail_operation(order["public_id"],"stripe_full_refund",token,"confirmed provider failure")
            target="none"
        else:
            if token: self.db.complete_operation(order["public_id"],"stripe_full_refund",token,result=state)
            target="refunded" if state=="succeeded" else "pending"
        current=self.db.get_order(order["public_id"])["refund_state"]
        logical=dedupe_key or f"refund:{refund['id']}:{state}"
        # Provider delivery order is not causal order.  Once success is known,
        # pending cannot regress it and failure is a contradiction, not reversal.
        if current=="refunded":
            if state=="failed": self._attention(order,refund,"failure_after_success",logical)
            self.db.append_order_event(order["public_id"],"refund_reconciled",source=actor,actor=actor,
                lifecycle="refund_state",old_value=current,new_value=current,
                metadata={"provider_status":state},dedupe_key=logical)
            return self.db.get_order(order["public_id"])
        if current=="manual_resolution" and state!="succeeded":
            self.db.append_order_event(order["public_id"],"refund_reconciled",source=actor,actor=actor,
                lifecycle="refund_state",old_value=current,new_value=current,
                metadata={"provider_status":state},dedupe_key=logical)
            return self.db.get_order(order["public_id"])
        return self.db.compare_and_set_refund(order["public_id"],current,target,actor=actor,
            metadata={"provider_status":state},stripe_refund_id=refund["id"],dedupe_key=logical) or self.db.get_order(order["public_id"])

    def reconcile(self, order, refund, actor="stripe_webhook"):
        """Reconcile provider evidence without ever initiating a refund."""
        status=str(refund.get("status") or "unknown").lower(); refund=dict(refund,status=status)
        logical=f"refund:{refund.get('id') or 'unknown'}:{status}"
        current=self.db.get_order(order["public_id"])
        existing=current.get("stripe_refund_id")
        conflict=bool(existing and refund.get("id") and existing!=refund["id"])
        try:
            self._verify(current,refund)
            if conflict: raise StripeIntegrationError("refund_identifier_conflict","Refund identifier requires manual review",ambiguous=True,provider_id=refund.get("id"))
        except StripeIntegrationError as exc:
            self._attention(current,refund,exc.kind,logical)
            if current["refund_state"]!="refunded" and current["refund_state"]!="manual_resolution":
                self.db.compare_and_set_refund(current["public_id"],current["refund_state"],"manual_resolution",actor=actor,
                    metadata={"kind":exc.kind},dedupe_key=logical, stripe_refund_id=refund.get("id"))
            else:
                self.db.append_order_event(current["public_id"],"refund_reconciliation_conflict",source=actor,actor=actor,
                    lifecycle="refund_state",old_value=current["refund_state"],new_value=current["refund_state"],
                    metadata={"kind":exc.kind},dedupe_key=logical)
            return self.db.get_order(current["public_id"])
        return self._record(current,refund,actor=actor,dedupe_key=logical)

    def recheck(self, public_id, actor="seller"):
        order=self.db.get_order(public_id)
        if not order or order["refund_state"] not in ("pending","manual_resolution"):
            raise RefundError("Refund cannot be rechecked")
        if not order.get("stripe_refund_id"): raise RefundError("No known Refund ID is available")
        try:
            refund=self.stripe.retrieve_refund(order["stripe_refund_id"])
            return self.reconcile(order,refund,actor=actor)
        except StripeIntegrationError as exc:
            if exc.ambiguous: return self.db.compare_and_set_refund(public_id,order["refund_state"],"manual_resolution",actor=actor,
                metadata={"kind":exc.kind}) or self.db.get_order(public_id)
            self.db.reconcile_operation_failure(public_id,"stripe_full_refund",json.dumps(exc.safe_dict(),sort_keys=True))
            return self.db.compare_and_set_refund(public_id,order["refund_state"],"none",actor=actor,
                metadata={"kind":exc.kind}) or self.db.get_order(public_id)
