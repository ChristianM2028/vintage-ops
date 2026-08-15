from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class InteractionKind(str, Enum):
    TEXTBOX = "textbox"
    TEXTAREA = "textarea"
    RICH_TEXT = "rich_text"
    NATIVE_SELECT = "native_select"
    COMBOBOX = "combobox"
    CLICK_SELECT = "click_select"
    HIERARCHICAL_SELECT = "hierarchical_select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    TOGGLE = "toggle"
    FILE_INPUT = "file_input"
    DROPZONE = "dropzone"
    TOKENIZED_INPUT = "tokenized_input"
    DATE_OR_NUMERIC = "date_or_numeric"


class VerificationMode(str, Enum):
    NORMALIZED_TEXT = "normalized_text"
    BOOLEAN = "boolean"
    SELECTED_OPTION = "selected_option"
    TOKEN_SET = "token_set"
    PATH = "path"
    FILES = "files"


class OccurrencePolicy(str, Enum):
    UNIQUE = "unique"
    FIRST_IN_SCOPE = "first_in_scope"


@dataclass(frozen=True)
class FieldValue:
    text: str | None = None
    number: int | float | None = None
    boolean: bool | None = None
    choice: str | None = None
    choices: tuple[str, ...] = ()
    category_path: tuple[str, ...] = ()
    photo_paths: tuple[str, ...] = ()

    def __post_init__(self):
        populated = sum((self.text is not None, self.number is not None,
                         self.boolean is not None, self.choice is not None,
                         bool(self.choices), bool(self.category_path), bool(self.photo_paths)))
        if populated != 1:
            raise ValueError("FieldValue requires exactly one value representation")

    @property
    def expected(self) -> Any:
        for name in ("text", "number", "boolean", "choice", "choices", "category_path", "photo_paths"):
            value = getattr(self, name)
            if value is not None and value != ():
                return value


@dataclass(frozen=True)
class VerificationSpec:
    mode: VerificationMode
    expected: Any


@dataclass(frozen=True)
class LocatorRecipe:
    strategy: str
    value: str
    role: str | None = None
    container_fingerprint: str | None = None
    route_fingerprint: str | None = None
    scope: str | None = None

    def __post_init__(self):
        if self.strategy not in {"label", "role", "stable_attribute", "relative", "css"}:
            raise ValueError("Unsupported locator strategy")
        if "javascript:" in self.value.casefold() or self.strategy == "javascript":
            raise ValueError("Locator recipes cannot contain executable JavaScript")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    aliases: tuple[str, ...]
    value: FieldValue
    required: bool = True
    expected_kinds: frozenset[InteractionKind] = frozenset()
    verification: VerificationSpec | None = None
    dependencies: tuple[str, ...] = ()
    occurrence: OccurrencePolicy = OccurrencePolicy.UNIQUE
    allow_probe: bool = False
    timeout_ms: int = 10_000
    exception_key: str | None = None
    permit_omission: bool = False
    value_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.key or not self.aliases:
            raise ValueError("A field key and at least one alias are required")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if self.required and self.permit_omission:
            raise ValueError("Required fields cannot permit omission")


@dataclass(frozen=True)
class CandidateEvidence:
    kind: str
    detail: str
    weight: float


@dataclass(frozen=True)
class FieldCandidate:
    recipe: LocatorRecipe
    tag: str
    role: str | None = None
    input_type: str | None = None
    accessible_name: str | None = None
    evidence: tuple[CandidateEvidence, ...] = ()
    score: float = 0.0
    penalties: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedField:
    candidate: FieldCandidate
    locator: Any
    runner_up_score: float = 0.0


@dataclass(frozen=True)
class ClassificationResult:
    kind: InteractionKind
    confidence: float
    static_evidence: tuple[str, ...] = ()
    probe_observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControlState:
    value: Any = None
    text: str | None = None
    checked: bool | None = None
    selected: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupportResult:
    supported: bool
    reason: str | None = None


@dataclass(frozen=True)
class ActionEvidence:
    handler: str
    observations: tuple[str, ...] = ()
    popup_scope: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    expected: Any = None
    observed: Any = None
    reason: str | None = None


@dataclass(frozen=True)
class ActionResult:
    before: ControlState
    after: ControlState
    handler_used: str
    verification: VerificationResult
    safe_diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractionContext:
    run_id: str
    operation: str
    timeout_ms: int = 10_000


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
