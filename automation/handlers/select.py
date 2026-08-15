from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind
from automation.verifier import verify_value


class NativeSelectHandler(BaseHandler):
    kind=InteractionKind.NATIVE_SELECT
    async def snapshot(self,page,control): return ControlState(value=await control.locator.input_value())
    async def apply(self,page,control,spec,context):
        await control.locator.select_option(label=str(spec.value.expected))
        return ActionEvidence("native_select",("selected exact label",))
    async def verify(self,page,control,spec,before,evidence):
        label=await control.locator.locator("option:checked").inner_text()
        return verify_value(spec.value.expected,label)
