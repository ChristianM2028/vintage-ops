from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind
from automation.verifier import verify_value


class TextHandler(BaseHandler):
    kind = InteractionKind.TEXTBOX
    async def snapshot(self,page,control): return ControlState(value=await control.locator.input_value())
    async def apply(self,page,control,spec,context):
        await control.locator.focus(); await control.locator.fill(str(spec.value.expected)); await control.locator.blur()
        return ActionEvidence("text",("focused","filled","blurred"))
    async def verify(self,page,control,spec,before,evidence): return verify_value(spec.value.expected,await control.locator.input_value())


class TextareaHandler(TextHandler):
    kind = InteractionKind.TEXTAREA
