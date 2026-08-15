from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind
from automation.verifier import verify_value
from automation.exceptions import OptionNotFound
class HierarchicalHandler(BaseHandler):
    kind=InteractionKind.HIERARCHICAL_SELECT
    async def snapshot(self,page,control): return ControlState(text=await control.locator.inner_text())
    async def apply(self,page,control,spec,context):
        picker=await page.open_scoped_popup(control.locator)
        for level in spec.value.category_path:
            option=picker.get_by_text(level,exact=True)
            if await option.count()!=1:
                raise OptionNotFound("The exact category level is missing or ambiguous",marketplace="unknown",operation=context.operation,field_key=spec.key,phase="action",run_id=context.run_id)
            await option.click(); await page.wait_for_rerender()
        return ActionEvidence("hierarchical",tuple(spec.value.category_path),"popup")
    async def verify(self,page,control,spec,before,evidence): return verify_value(spec.value.category_path[-1],await control.locator.inner_text())
