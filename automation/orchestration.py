from .repository import AutomationRepository

class BrowserCommandAdapter:
    """Synchronous application adapter that only persists and enqueues work."""
    def __init__(self,name,db,queue): self.name=name; self.db=db; self.repository=AutomationRepository(db); self.queue=queue
    def _enqueue(self,item,operation):
        run=self.repository.create_run(item["id"],self.name,operation)
        self.queue.enqueue(run["id"])
        result=dict(item); result["automation_run_id"]=run["id"]; result["automation_state"]="queued"; return result
    def list(self,item): return self._enqueue(item,"list")
    def update(self,item): return self._enqueue(item,"update")
    def delist(self,item): return self._enqueue(item,"delist")
