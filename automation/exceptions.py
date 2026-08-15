from __future__ import annotations

from typing import Any


class AutomationError(Exception):
    code = "automation_error"

    def __init__(self, message: str, *, marketplace: str, operation: str,
                 phase: str, run_id: str, field_key: str | None = None,
                 resolution_confidence: float | None = None,
                 classification_confidence: float | None = None,
                 evidence: list[dict[str, Any]] | None = None,
                 route: str | None = None, next_action: str = "review_run"):
        super().__init__(message)
        self.context = {
            "code": self.code, "message": message, "marketplace": marketplace,
            "operation": operation, "field_key": field_key, "phase": phase,
            "resolution_confidence": resolution_confidence,
            "classification_confidence": classification_confidence,
            "candidate_evidence": evidence or [], "route": route,
            "run_id": run_id, "recommended_next_action": next_action,
        }

    def safe_dict(self):
        from .diagnostics import redact
        return redact(self.context)


def _error(name, code):
    return type(name, (AutomationError,), {"code": code})


AuthenticationRequired = _error("AuthenticationRequired", "authentication_required")
FieldNotFound = _error("FieldNotFound", "field_not_found")
AmbiguousField = _error("AmbiguousField", "ambiguous_field")
LowResolutionConfidence = _error("LowResolutionConfidence", "low_resolution_confidence")
UnsupportedInteraction = _error("UnsupportedInteraction", "unsupported_interaction")
LowClassificationConfidence = _error("LowClassificationConfidence", "low_classification_confidence")
OptionNotFound = _error("OptionNotFound", "option_not_found")
VerificationFailed = _error("VerificationFailed", "verification_failed")
PageChanged = _error("PageChanged", "page_changed")
UploadFailed = _error("UploadFailed", "upload_failed")
RateLimited = _error("RateLimited", "rate_limited")
ChallengeDetected = _error("ChallengeDetected", "challenge_detected")
SubmissionUncertain = _error("SubmissionUncertain", "submission_uncertain")
RemoteValidationFailed = _error("RemoteValidationFailed", "remote_validation_failed")

# Names retained from the design prose as convenient aliases.
AmbiguousFieldError = AmbiguousField
