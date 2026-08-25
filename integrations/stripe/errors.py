class StripeIntegrationError(Exception):
    """A deliberately sanitized payment-provider failure."""
    def __init__(self, kind, message, ambiguous=False, provider_id=None):
        super().__init__(message)
        self.kind, self.ambiguous = kind, ambiguous
        self.provider_id = provider_id

    def safe_dict(self):
        return {"kind": self.kind, "message": str(self), "ambiguous": self.ambiguous}
