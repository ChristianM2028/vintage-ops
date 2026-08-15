from __future__ import annotations

from typing import Protocol

from automation.models import (ActionEvidence, ControlState, FieldSpec,
    InteractionContext, InteractionKind, ResolvedField, SupportResult,
    VerificationResult)


class InteractionHandler(Protocol):
    kind: InteractionKind
    async def supports(self, control: ResolvedField, spec: FieldSpec) -> SupportResult: ...
    async def snapshot(self, page, control: ResolvedField) -> ControlState: ...
    async def apply(self, page, control: ResolvedField, spec: FieldSpec,
                    context: InteractionContext) -> ActionEvidence: ...
    async def verify(self, page, control: ResolvedField, spec: FieldSpec,
                     before: ControlState, evidence: ActionEvidence) -> VerificationResult: ...
    async def recover(self, page, evidence: ActionEvidence) -> None: ...


class BaseHandler:
    kind: InteractionKind

    async def supports(self, control, spec):
        return SupportResult(self.kind in spec.expected_kinds or not spec.expected_kinds)

    async def recover(self, page, evidence):
        if evidence.popup_scope:
            await page.keyboard.press("Escape")
