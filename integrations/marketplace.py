from typing import Protocol

class MarketplaceAdapter(Protocol):
    """Application boundary; browser internals remain behind implementations."""
    name: str
    def list(self, item: dict) -> dict: ...
    def update(self, item: dict) -> dict: ...
    def delist(self, item: dict) -> dict: ...


class MarketplaceRegistry:
    def __init__(self): self._implementations = {}
    def register(self, marketplace, implementation, adapter):
        self._implementations[(marketplace, implementation)] = adapter
    def get(self, marketplace, implementation="api"):
        try: return self._implementations[(marketplace, implementation)]
        except KeyError: raise ValueError(f"No {implementation} implementation is configured for {marketplace}") from None
