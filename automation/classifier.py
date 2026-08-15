from __future__ import annotations

from dataclasses import dataclass

from .exceptions import LowClassificationConfidence, UnsupportedInteraction
from .models import ClassificationResult, FieldSpec, InteractionKind, ResolvedField


@dataclass(frozen=True)
class ClassifierConfig:
    action_threshold: float = .85


class InteractionClassifier:
    def __init__(self, config=None): self.config = config or ClassifierConfig()

    def static(self, control: ResolvedField, spec: FieldSpec) -> ClassificationResult | None:
        c = control.candidate; tag=(c.tag or "").lower(); role=(c.role or "").lower(); typ=(c.input_type or "text").lower()
        kind = None; evidence=[]
        if tag == "textarea": kind=InteractionKind.TEXTAREA
        elif tag == "select": kind=InteractionKind.NATIVE_SELECT
        elif tag == "input" and typ == "file": kind=InteractionKind.FILE_INPUT
        elif tag == "input" and typ == "checkbox": kind=InteractionKind.CHECKBOX
        elif tag == "input" and typ == "radio": kind=InteractionKind.RADIO
        elif role in {"checkbox", "radio", "switch"}: kind={"checkbox":InteractionKind.CHECKBOX,"radio":InteractionKind.RADIO,"switch":InteractionKind.TOGGLE}[role]
        elif c.metadata.get("contenteditable") or c.metadata.get("multiline"): kind=InteractionKind.RICH_TEXT
        elif c.metadata.get("multiselect") and c.metadata.get("tokens"): kind=InteractionKind.TOKENIZED_INPUT
        elif role == "combobox" or c.metadata.get("aria_autocomplete") or c.metadata.get("owns_listbox"): kind=InteractionKind.COMBOBOX
        elif c.metadata.get("haspopup") in {"tree", "dialog"}: kind=InteractionKind.HIERARCHICAL_SELECT
        elif c.metadata.get("haspopup") in {"listbox", "menu"}: kind=InteractionKind.CLICK_SELECT
        elif tag == "input" and typ in {"date","datetime-local","month","number","time","week"}: kind=InteractionKind.DATE_OR_NUMERIC
        elif tag == "input" and typ in {"text","email","search","tel","url","password"}: kind=InteractionKind.TEXTBOX
        if not kind: return None
        evidence.append(f"static:{tag}:{role}:{typ}")
        confidence = .98 if not spec.expected_kinds or kind in spec.expected_kinds else .45
        return ClassificationResult(kind, confidence, tuple(evidence))

    async def classify(self, page, control, spec, **context):
        result=self.static(control,spec)
        if (not result or result.confidence < self.config.action_threshold) and spec.allow_probe:
            # The browser collector owns the single reversible click and blocks
            # destructive accessible names before it is called.
            observation=await page.probe_control(control.locator, destructive_names=("publish","list it","delete","payment","confirm"))
            if observation.get("kind"):
                result=ClassificationResult(InteractionKind(observation["kind"]), observation.get("confidence",0), (), tuple(observation.get("observations",())))
        if not result:
            raise UnsupportedInteraction("The control pattern is unsupported", phase="classification", **context)
        if result.confidence < self.config.action_threshold or (spec.expected_kinds and result.kind not in spec.expected_kinds):
            raise LowClassificationConfidence("Interaction classification is unsafe", phase="classification", classification_confidence=result.confidence, **context)
        return result
