from .errors import StripeIntegrationError

class StripeAdapter:
    identity = "stripe_test"
    def __init__(self, config): self.cfg=config
    def create_checkout(self, order, item, success_url, cancel_url):
        import stripe
        try:
            result=stripe.checkout.Session.create(
                api_key=self.cfg.stripe_secret_key,
                mode="payment", payment_intent_data={"capture_method":"manual","metadata":{"order":order["public_id"]}},
                payment_method_types=["card"],
                shipping_address_collection={"allowed_countries":list(self.cfg.stripe_shipping_countries)},
                line_items=[{"quantity":1,"price_data":{"currency":order["currency_snapshot"].lower(),"unit_amount":int(round(float(order["price_snapshot"])*100)),"product_data":{"name":item["title"][:120]}}}],
                success_url=success_url, cancel_url=cancel_url, client_reference_id=order["public_id"],
                idempotency_key=f"checkout:{order['public_id']}")
            return {"id":result.id,"url":result.url,"payment_intent":getattr(result,"payment_intent",None)}
        except Exception as exc:
            raise StripeIntegrationError("provider_error","Payment checkout is temporarily unavailable",ambiguous=True) from exc
    def retrieve_session(self, session_id):
        import stripe
        try:
            return stripe.checkout.Session.retrieve(session_id,api_key=self.cfg.stripe_secret_key,expand=["payment_intent"])
        except Exception as exc:
            # SDK exception classes vary between supported Stripe releases. Only
            # the stable class name/status are inspected and neither is exposed.
            name=type(exc).__name__.lower(); status=getattr(exc,"http_status",None)
            if name in ("invalidrequesterror",) and status==404:
                err=StripeIntegrationError("session_not_found","Checkout session was not found",ambiguous=False)
            elif "authentication" in name or status in (401,403):
                err=StripeIntegrationError("provider_identity","Checkout account requires review",ambiguous=True)
            elif any(x in name for x in ("connection","timeout","apierror")):
                err=StripeIntegrationError("transport","Checkout provider is temporarily unavailable",ambiguous=True)
            else:
                err=StripeIntegrationError("session_retrieval_ambiguous","Checkout session requires review",ambiguous=True)
            raise err from exc
    def capture(self, payment_intent_id, key):
        import stripe
        if not payment_intent_id: raise StripeIntegrationError("missing_payment_intent","Payment authorization is not ready")
        try: return stripe.PaymentIntent.capture(payment_intent_id,api_key=self.cfg.stripe_secret_key,idempotency_key=key)
        except Exception as exc: raise StripeIntegrationError("capture_error","Capture result requires review",ambiguous=True) from exc
    def cancel(self, payment_intent_id, key):
        import stripe
        if not payment_intent_id: raise StripeIntegrationError("missing_payment_intent","Payment authorization is not ready")
        try: return stripe.PaymentIntent.cancel(payment_intent_id,api_key=self.cfg.stripe_secret_key,idempotency_key=key)
        except Exception as exc: raise StripeIntegrationError("cancellation_error","Cancellation result requires review",ambiguous=True) from exc
    @staticmethod
    def _refund(result):
        payment_intent=getattr(result,"payment_intent",None)
        return {"id":getattr(result,"id",None),"payment_intent":getattr(payment_intent,"id",payment_intent),
                "amount":getattr(result,"amount",None),"currency":str(getattr(result,"currency","")).upper(),
                "status":getattr(result,"status",None)}
    @staticmethod
    def _refund_error(exc):
        name=type(exc).__name__.lower(); status=getattr(exc,"http_status",None)
        provider_id=getattr(getattr(exc,"error",None),"refund",None)
        if name in ("invalidrequesterror","carderror","idempotencyerror") or status in (400,402,404):
            return StripeIntegrationError("refund_rejected","Stripe rejected the refund",ambiguous=False,provider_id=provider_id)
        return StripeIntegrationError("refund_ambiguous","Refund outcome requires reconciliation",ambiguous=True,provider_id=provider_id)
    def create_full_refund(self, payment_intent_id, amount, currency, public_id):
        import stripe
        if not payment_intent_id: raise StripeIntegrationError("missing_payment_intent","PaymentIntent is not available")
        try:
            result=stripe.Refund.create(payment_intent=payment_intent_id,amount=amount,
                api_key=self.cfg.stripe_secret_key,idempotency_key=f"refund:{public_id}:full:v1")
            return self._refund(result)
        except Exception as exc: raise self._refund_error(exc) from exc
    def retrieve_refund(self, refund_id):
        import stripe
        if not refund_id: raise StripeIntegrationError("missing_refund","A known Refund ID is required",ambiguous=False)
        try: return self._refund(stripe.Refund.retrieve(refund_id,api_key=self.cfg.stripe_secret_key))
        except Exception as exc: raise self._refund_error(exc) from exc
    def verify_webhook(self, payload, signature):
        import stripe
        try: return stripe.Webhook.construct_event(payload,signature,self.cfg.stripe_webhook_secret)
        except Exception as exc: raise StripeIntegrationError("invalid_signature","Invalid webhook signature") from exc
