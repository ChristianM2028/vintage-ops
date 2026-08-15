from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .exceptions import AmbiguousField, FieldNotFound, LowResolutionConfidence
from .models import CandidateEvidence, FieldCandidate, FieldSpec, ResolvedField


@dataclass(frozen=True)
class ResolverConfig:
    explicit_label: float = 40
    accessible_name: float = 35
    group_label: float = 25
    compatible_type: float = 15
    stable_attribute: float = 10
    structural_text: float = 12
    geometry: float = 5
    write_threshold: float = .80
    score_margin: float = .15
    maximum_score: float = 100


def normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[\u2018\u2019'\"`]+", "", value)
    value = re.sub(r"[^\w\s&/+.-]", " ", value)
    return " ".join(value.split())


def alias_matches(alias: str, observed: str) -> bool:
    alias, observed = normalize_label(alias), normalize_label(observed)
    if alias == observed:
        return True
    # Short labels are intentionally never substring matched (e.g. Size).
    return len(alias) >= 8 and len(alias.split()) >= 2 and alias in observed.split(" - ")


class SemanticResolver:
    def __init__(self, config: ResolverConfig | None = None):
        self.config = config or ResolverConfig()

    def score(self, candidate: FieldCandidate) -> FieldCandidate:
        raw = sum(max(0, e.weight) for e in candidate.evidence)
        score = min(1.0, raw / self.config.maximum_score)
        return FieldCandidate(**{**candidate.__dict__, "score": score})

    def choose(self, candidates: Iterable[FieldCandidate], spec: FieldSpec, *,
               marketplace="unknown", operation="list", run_id="unknown", route=None):
        ranked = sorted((self.score(c) for c in candidates if not c.penalties),
                        key=lambda c: c.score, reverse=True)
        context = dict(marketplace=marketplace, operation=operation,
                       field_key=spec.key, phase="resolution", run_id=run_id, route=route)
        if not ranked:
            raise FieldNotFound("No safe control matched the field", **context)
        evidence = [{"recipe": c.recipe.strategy, "tag": c.tag,
                     "score": round(c.score, 3),
                     "evidence": [e.kind for e in c.evidence]} for c in ranked[:5]]
        top, runner = ranked[0], ranked[1].score if len(ranked) > 1 else 0.0
        if top.score < self.config.write_threshold:
            raise LowResolutionConfidence("Field score is below the write threshold",
                resolution_confidence=top.score, evidence=evidence, **context)
        if top.score - runner < self.config.score_margin:
            raise AmbiguousField("The leading controls do not have a safe score margin",
                resolution_confidence=top.score, evidence=evidence, **context)
        return top, runner

    async def resolve(self, page, spec: FieldSpec, **context) -> ResolvedField:
        """Resolve through a page-provided semantic snapshot.

        Browser bindings build candidates from the active document, dialogs and
        same-origin frames. Keeping snapshot collection separate makes scoring
        deterministic and fixture-testable; cross-origin frames are reported by
        the collector rather than silently ignored.
        """
        snapshot = await page.semantic_candidates(spec.aliases, spec.expected_kinds)
        candidate, runner = self.choose(snapshot, spec, **context)
        locator = await page.locator_from_recipe(candidate.recipe)
        return ResolvedField(candidate, locator, runner)
