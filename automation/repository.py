import json, secrets
from db import now
from .diagnostics import redact

class AutomationRepository:
    def __init__(self,db): self.db=db
    def create_run(self,item_id,marketplace,operation,idempotency_key=None):
        run_id=secrets.token_urlsafe(18); stamp=now(); key=idempotency_key or f"{marketplace}:{operation}:{item_id}:{run_id}"
        with self.db.transaction(immediate=True) as c:
            c.execute("INSERT INTO automation_runs(id,item_id,marketplace,operation,state,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(run_id,item_id,marketplace,operation,"queued",key,stamp,stamp))
            c.execute("INSERT INTO marketplace_listings(item_id,marketplace,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(item_id,marketplace) DO NOTHING",(item_id,marketplace,stamp,stamp))
        return self.get_run(run_id)
    def get_run(self,run_id):
        with self.db.connect() as c:
            row=c.execute("SELECT * FROM automation_runs WHERE id=?",(run_id,)).fetchone()
            return dict(row) if row else None
    def get_run_attempts(self,run_id):
        with self.db.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM automation_field_attempts WHERE run_id=? ORDER BY id",(run_id,)).fetchall()]
    def get_run_detail(self,run_id):
        run=self.get_run(run_id)
        if run is not None: run["field_attempts"]=self.get_run_attempts(run_id)
        return run
    def set_run_state(self,run_id,state,error=None,checkpoint=None):
        safe=json.dumps(redact(error)) if error else None
        with self.db.transaction() as c: c.execute("UPDATE automation_runs SET state=?,safe_error_json=?,checkpoint_field=COALESCE(?,checkpoint_field),updated_at=? WHERE id=?",(state,safe,checkpoint,now(),run_id))
    def record_field(self,run_id,key,state,*,handler=None,resolution=None,classification=None,evidence=None,error=None):
        stamp=now()
        with self.db.transaction(immediate=True) as c:
            attempt=c.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM automation_field_attempts WHERE run_id=? AND field_key=?",(run_id,key)).fetchone()[0]
            c.execute("INSERT INTO automation_field_attempts(run_id,field_key,attempt,state,handler,resolution_confidence,classification_confidence,evidence_json,safe_error_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(run_id,key,attempt,state,handler,resolution,classification,json.dumps(redact(evidence or {})),json.dumps(redact(error)) if error else None,stamp,stamp))
        if state=="verified": self.set_run_state(run_id,"running",checkpoint=key)
    def update_listing(self,item_id,marketplace,**values):
        allowed={"remote_id","remote_url","lifecycle_state","last_successful_operation","safe_error_json"}
        if set(values)-allowed: raise ValueError("Unsupported listing field")
        if "safe_error_json" in values and not isinstance(values["safe_error_json"],str): values["safe_error_json"]=json.dumps(redact(values["safe_error_json"]))
        values["updated_at"]=now()
        with self.db.transaction() as c: c.execute("UPDATE marketplace_listings SET "+",".join(f"{k}=?" for k in values)+" WHERE item_id=? AND marketplace=?",[*values.values(),item_id,marketplace])
