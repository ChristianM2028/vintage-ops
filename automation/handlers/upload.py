from pathlib import Path
from .base import BaseHandler
from automation.models import ActionEvidence, ControlState, InteractionKind, VerificationResult


class UploadHandler(BaseHandler):
    kind=InteractionKind.FILE_INPUT
    async def snapshot(self,page,control): return ControlState(files=tuple(await control.locator.evaluate("e => [...e.files].map(f => f.name)")))
    async def apply(self,page,control,spec,context):
        paths=tuple(spec.value.photo_paths); await control.locator.set_input_files(list(paths))
        await page.wait_for_uploads_complete()
        return ActionEvidence("upload",tuple(f"file:{Path(p).name}" for p in paths))
    async def verify(self,page,control,spec,before,evidence):
        actual=tuple(await control.locator.evaluate("e => [...e.files].map(f => f.name)")); expected=tuple(Path(p).name for p in spec.value.photo_paths)
        errors=await page.upload_errors(); return VerificationResult(actual==expected and not errors,expected,actual,"upload errors" if errors else None)
