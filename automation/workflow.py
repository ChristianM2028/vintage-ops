from .exceptions import SubmissionUncertain, VerificationFailed

def _safe_error(exc):
    return exc.safe_dict() if hasattr(exc,"safe_dict") else {"code":"field_failed","message":"Field automation failed safely"}

class ListingWorkflow:
    def __init__(self,adapter,engine,repository,context_factory): self.adapter=adapter; self.engine=engine; self.repository=repository; self.context_factory=context_factory
    async def execute(self,run):
        item=self.repository.db.get_item(run["item_id"]); specs=self.adapter.field_specs(item,run["operation"])
        async with self.context_factory(self.adapter,run) as page:
            await self.adapter.bootstrap(page,run["operation"])
            for spec in specs:
                self.repository.record_field(run["id"],spec.key,"started",evidence={"phase":"write"})
                try:
                    result=await self.engine.execute(page,spec,marketplace=self.adapter.name,operation=run["operation"],run_id=run["id"],route=self.adapter.route_name(page.url))
                except Exception as exc:
                    safe=_safe_error(exc); self.repository.record_field(run["id"],spec.key,"failed",resolution=safe.get("resolution_confidence"),classification=safe.get("classification_confidence"),evidence=safe.get("candidate_evidence"),error=safe); raise
                self.repository.record_field(run["id"],spec.key,"verified",handler=result.handler_used,resolution=result.safe_diagnostics["resolution"],classification=result.safe_diagnostics["classification"],evidence={"verification":result.verification.__dict__})
            for spec in (s for s in specs if s.required):
                self.repository.record_field(run["id"],spec.key,"started",evidence={"phase":"audit"})
                try:
                    audit=await self.engine.audit(page,spec,marketplace=self.adapter.name,operation="audit",run_id=run["id"],route=self.adapter.route_name(page.url))
                    if not audit.verification.verified: raise VerificationFailed("Pre-submit audit failed",marketplace=self.adapter.name,operation=run["operation"],field_key=spec.key,phase="audit",run_id=run["id"])
                except Exception as exc:
                    safe=_safe_error(exc); self.repository.record_field(run["id"],spec.key,"failed",resolution=safe.get("resolution_confidence"),classification=safe.get("classification_confidence"),evidence=safe.get("candidate_evidence"),error=safe); raise
                self.repository.record_field(run["id"],spec.key,"audit_succeeded",handler=audit.handler_used,resolution=audit.safe_diagnostics["resolution"],classification=audit.safe_diagnostics["classification"],evidence={"verification":audit.verification.__dict__})
            if run["operation"]=="list":
                result=await self.adapter.submit_once(page,run["id"])
                if result.get("uncertain"): raise SubmissionUncertain("Publication result is uncertain",marketplace=self.adapter.name,operation="list",phase="submission",run_id=run["id"],next_action="reconcile_remote_listings")
                self.repository.update_listing(item["id"],self.adapter.name,remote_id=result.get("remote_id"),remote_url=result.get("remote_url"),lifecycle_state="active",last_successful_operation="list",safe_error_json=None)
