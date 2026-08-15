from collections import deque
from dataclasses import dataclass

@dataclass(frozen=True)
class AutomationJob: run_id: str

class InProcessAutomationQueue:
    """Small command boundary; replaceable by an external worker unchanged."""
    def __init__(self): self._jobs=deque()
    def enqueue(self,run_id): self._jobs.append(AutomationJob(run_id))
    def claim(self): return self._jobs.popleft() if self._jobs else None

class AutomationWorker:
    def __init__(self,repository,workflows): self.repository=repository; self.workflows=workflows
    async def execute(self,job):
        run=self.repository.get_run(job.run_id); self.repository.set_run_state(job.run_id,"running")
        try: await self.workflows[run["marketplace"]].execute(run)
        except Exception as exc:
            safe=exc.safe_dict() if hasattr(exc,"safe_dict") else {"code":"worker_failed","message":"Automation failed safely"}
            self.repository.set_run_state(job.run_id,"failed",safe); return
        self.repository.set_run_state(job.run_id,"succeeded")
