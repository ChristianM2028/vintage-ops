from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind
from automation.verifier import verify_value
from automation.exceptions import OptionNotFound


class ClickSelectHandler(BaseHandler):
    kind=InteractionKind.CLICK_SELECT
    async def snapshot(self,page,control): return ControlState(text=await control.locator.inner_text())
    async def apply(self,page,control,spec,context):
        popup=await page.open_scoped_popup(control.locator)
        option=popup.get_by_role("option",name=str(spec.value.expected),exact=True)
        if await option.count() != 1:
            raise OptionNotFound("The exact option is missing or ambiguous",marketplace="unknown",operation=context.operation,field_key=spec.key,phase="action",run_id=context.run_id)
        await option.click()
        return ActionEvidence("click_select",("exact option selected",),"popup")
    async def verify(self,page,control,spec,before,evidence): return verify_value(spec.value.expected,await control.locator.inner_text())


class ComboboxHandler(ClickSelectHandler): kind=InteractionKind.COMBOBOX
