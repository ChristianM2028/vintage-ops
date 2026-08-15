from .base import BaseHandler
from automation.models import ActionEvidence,ControlState,InteractionKind
from automation.verifier import verify_value
class RichTextHandler(BaseHandler):
    kind=InteractionKind.RICH_TEXT
    async def snapshot(self,page,control): return ControlState(text=await control.locator.inner_text())
    async def apply(self,page,control,spec,context):
        await control.locator.focus(); await control.locator.press("ControlOrMeta+A"); await control.locator.fill(str(spec.value.expected)); await control.locator.blur()
        return ActionEvidence("rich_text",("editable root filled","blurred"))
    async def verify(self,page,control,spec,before,evidence): return verify_value(spec.value.expected,await control.locator.inner_text())
