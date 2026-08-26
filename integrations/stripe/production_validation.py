"""Read-only validation of the configured Stripe production identity."""
from .errors import StripeIntegrationError


class ProductionStripeValidator:
    def __init__(self, config, retriever=None):
        self.cfg=config; self.retriever=retriever

    def validate(self):
        if self.cfg.stripe_provider_mode != "live" or not self.cfg.stripe_secret_key.startswith("sk_live_"):
            raise StripeIntegrationError("configuration","Stripe validation requires live mode",ambiguous=False)
        if not isinstance(self.cfg.expected_stripe_account_id,str) or not self.cfg.expected_stripe_account_id.strip():
            raise StripeIntegrationError("configuration","Expected Stripe account identity is required",ambiguous=False)
        try:
            if self.retriever:
                account=self.retriever(self.cfg.stripe_secret_key)
            else:
                import stripe
                account=stripe.Account.retrieve(api_key=self.cfg.stripe_secret_key)
            account_id=account.get("id") if isinstance(account,dict) else getattr(account,"id",None)
            livemode=account.get("livemode") if isinstance(account,dict) else getattr(account,"livemode",None)
            if not isinstance(account_id,str) or not account_id: raise ValueError("missing id")
            if livemode is not True: raise StripeIntegrationError("provider_mode","Stripe account is not live",ambiguous=False)
            if account_id != self.cfg.expected_stripe_account_id: raise StripeIntegrationError("provider_identity","Stripe account identity mismatch",ambiguous=False)
            return {"ok":True,"provider":"stripe","account_id":account_id,"livemode":True}
        except StripeIntegrationError: raise
        except Exception as exc:
            raise StripeIntegrationError("provider_validation","Stripe account validation failed",ambiguous=False) from exc
