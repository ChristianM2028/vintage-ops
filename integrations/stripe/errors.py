class StripeIntegrationError(Exception):
    """A deliberately sanitized payment-provider failure."""
    def __init__(self, kind, message, ambiguous=False):
        super().__init__(message)
        self.kind, self.ambiguous = kind, ambiguous

    def safe_dict(self):
        return {"kind": self.kind, "message": str(self), "ambiguous": self.ambiguous}
