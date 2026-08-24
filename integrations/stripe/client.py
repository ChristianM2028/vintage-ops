from .errors import StripeIntegrationError

class StripeAdapter:
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
        return stripe.checkout.Session.retrieve(session_id,api_key=self.cfg.stripe_secret_key,expand=["payment_intent"])
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
    def verify_webhook(self, payload, signature):
        import stripe
        try: return stripe.Webhook.construct_event(payload,signature,self.cfg.stripe_webhook_secret)
        except Exception as exc: raise StripeIntegrationError("invalid_signature","Invalid webhook signature") from exc
