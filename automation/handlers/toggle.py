from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind, VerificationResult


class ToggleHandler(BaseHandler):
    kind=InteractionKind.TOGGLE
    async def snapshot(self,page,control): return ControlState(checked=await control.locator.is_checked())
    async def apply(self,page,control,spec,context):
        desired=bool(spec.value.expected)
        if await control.locator.is_checked() != desired: await control.locator.set_checked(desired)
        return ActionEvidence("toggle",(f"set:{desired}",))
    async def verify(self,page,control,spec,before,evidence):
        actual=await control.locator.is_checked(); expected=bool(spec.value.expected)
        return VerificationResult(actual == expected,expected,actual)


class CheckboxHandler(ToggleHandler): kind=InteractionKind.CHECKBOX
