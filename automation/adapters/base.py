from __future__ import annotations
from typing import Protocol, Sequence
from automation.models import FieldSpec

class BrowserMarketplaceAdapter(Protocol):
    name: str
    version: str
    concurrency_limit: int
    def field_specs(self,item:dict,operation:str)->Sequence[FieldSpec]: ...
    async def bootstrap(self,page,operation:str)->None: ...
    async def submit_once(self,page,run_id:str)->dict: ...
    async def reconcile(self,page,item:dict)->dict: ...
    def route_name(self,url:str)->str: ...

class UninspectedAdapter:
    """Intentional placeholder: no routes or aliases are guessed."""
    version="uninspected"
    def field_specs(self,item,operation): raise NotImplementedError("Authorized fixtures are required")
