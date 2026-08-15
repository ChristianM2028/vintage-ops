from typing import Protocol

class MarketplaceAdapter(Protocol):
    """The deliberately small boundary implemented by registered marketplaces."""
    name: str
    def list(self, item: dict) -> dict: ...
    def delist(self, item: dict) -> dict: ...
