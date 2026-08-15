from .exceptions import VerificationFailed
from .handlers import default_handlers
from .models import ActionEvidence, ActionResult, InteractionContext

class FieldEngine:
    def __init__(self,resolver,classifier,handlers=None):
        self.resolver=resolver; self.classifier=classifier; self.handlers=handlers or default_handlers()
    async def execute(self,page,spec,*,marketplace,operation,run_id,route=None):
        ctx=dict(marketplace=marketplace,operation=operation,run_id=run_id,field_key=spec.key,route=route)
        control=await self.resolver.resolve(page,spec,**ctx)
        classification=await self.classifier.classify(page,control,spec,**ctx)
        handler=self.handlers[classification.kind]; before=await handler.snapshot(page,control)
        evidence=await handler.apply(page,control,spec,InteractionContext(run_id,operation,spec.timeout_ms))
        verification=await handler.verify(page,control,spec,before,evidence)
        after=await handler.snapshot(page,control)
        if not verification.verified:
            await handler.recover(page,evidence)
            raise VerificationFailed("Field state did not match the requested value",phase="verification",**ctx)
        return ActionResult(before,after,handler.__class__.__name__,verification,{"resolution":control.candidate.score,"classification":classification.confidence})

    async def audit(self,page,spec,*,marketplace,operation,run_id,route=None):
        """Re-resolve and read a field without performing another write."""
        ctx=dict(marketplace=marketplace,operation=operation,run_id=run_id,field_key=spec.key,route=route)
        control=await self.resolver.resolve(page,spec,**ctx)
        classification=await self.classifier.classify(page,control,spec,**ctx)
        handler=self.handlers[classification.kind]; current=await handler.snapshot(page,control)
        verification=await handler.verify(page,control,spec,current,ActionEvidence("audit",("read-only",)))
        if not verification.verified:
            raise VerificationFailed("Required field failed the read-only pre-submit audit",phase="audit",**ctx)
        return ActionResult(current,current,handler.__class__.__name__,verification,{"resolution":control.candidate.score,"classification":classification.confidence})
