"""AcadPulse adapter around the legacy repository's DBATU/ECE mark formulas.

This service delegates grade/result/SGPA/CGPA mathematics to
`legacy_calculations.py`. It supplies a clean input/output interface for the
new schema without calling the old repository's ORM updater.

Preview calculations are non-authoritative. To mark a result authoritative,
callers must supply an active AcademicPolicy whose rules_json names the exact
legacy profile ID. The policy gate is intentionally strict.
"""
from types import SimpleNamespace
from typing import Any
from decimal import Decimal, InvalidOperation

from .academic_policy import PolicyPendingError, require_active_policy
from .legacy_calculations import (
    calculate_cgpa,
    calculate_sgpa,
    compute_final_result,
    compute_lab_result,
    get_grade,
    is_gradable_subject,
    is_theory_internal_complete,
    update_internal_totals,
    validate_external_marks,
    validate_lab_component,
)

LEGACY_PROFILE_ID = "dbatu_ece_legacy_calculations_v1"
LEGACY_SOURCE = "Student-analysis-system-/calculations.py and final_result_utils.py"


def _policy_rules(policy: Any) -> dict:
    if isinstance(policy, dict):
        return policy.get("rules_json") or {}
    return getattr(policy, "rules_json", None) or {}


def _require_profile(policy: Any, purpose: str) -> None:
    require_active_policy(policy, purpose)
    if _policy_rules(policy).get("calculation_profile") != LEGACY_PROFILE_ID:
        raise PolicyPendingError(
            f"The active policy for {purpose} is not mapped to {LEGACY_PROFILE_ID}."
        )


def _normalize_credits(credits: Any) -> int:
    """The previous calculation module expects integer subject credits."""
    try:
        value = Decimal(str(credits))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Credits must be an integer in the legacy calculation profile.") from exc
    if value < 0 or value != value.to_integral_value():
        raise ValueError("Credits must be a non-negative integer for legacy calculation compatibility.")
    return int(value)


def _validate_range(value: Any, maximum: float, label: str) -> None:
    if value is None or str(value).strip() == "":
        return  # Missing marks remain pending; they are not converted to zero.
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not 0 <= number <= maximum:
        raise ValueError(f"{label} must be between 0 and {maximum:g}.")


def calculate_theory(
    *, ct1: Any, ct2: Any, assignment: Any, midsem: Any, external: Any,
    credits: int, policy: Any = None, authoritative: bool = False,
) -> dict:
    """Calculate a theory subject using the exact previous-repository formula.

    CT1, CT2 and Assignment are each /10. Midsem is /20. Internal = midsem +
    the two highest marks among CT1/CT2/Assignment, out of 40. External is /60.
    Missing components remain pending. External and components are range checked
    at the boundary so invalid data cannot enter calculations silently.
    """
    normalized_credits = _normalize_credits(credits)
    if authoritative:
        _require_profile(policy, "theory marks")
    _validate_range(ct1, 10, "CT1")
    _validate_range(ct2, 10, "CT2")
    _validate_range(assignment, 10, "Assignment")
    _validate_range(midsem, 20, "Midsem")
    valid, error = validate_external_marks(external, "THEORY")
    if external is not None and str(external).strip() and not valid:
        raise ValueError(error or "Invalid theory external marks.")

    row = SimpleNamespace(
        ct1=ct1, ct2=ct2, assignment=assignment, midsem=midsem,
        best_ct=None, ca_marks=None, internal_total=None,
    )
    update_internal_totals(row)
    complete = is_theory_internal_complete(row)

    if not complete or external is None or str(external).strip() == "":
        result = {
            "internal": float(row.internal_total) if row.internal_total is not None else None,
            "external": None if external is None or str(external).strip() == "" else float(external),
            "total": None, "grade": None, "grade_point": None,
            "credits_registered": normalized_credits, "credits_earned": 0,
            "is_passed": None,
            "fail_reason": "Internal marks not complete" if not complete else "External marks not entered",
        }
    else:
        result = compute_final_result(row.internal_total, external, normalized_credits)
    result.update({
        "best_internal_component": row.best_ct,
        "continuous_assessment_total": row.ca_marks,
        "internal_total": row.internal_total,
        "formula_profile": LEGACY_PROFILE_ID,
        "source_reference": LEGACY_SOURCE,
        "calculation_authority": "official-policy" if authoritative else "provisional-preview",
    })
    return result


def calculate_lab(
    *, ca1: Any, ca2: Any, external: Any, credits: int,
    legacy_internal: Any = None, policy: Any = None, authoritative: bool = False,
) -> dict:
    """Calculate a lab/project using CA1/30 + CA2/30 + external/40.

    For data compatibility only, if both CA fields are absent and legacy_internal
    is supplied, the previous repository's `final_result_utils.py` fallback is
    preserved (legacy internal + external, pass threshold total >= 40).
    """
    if authoritative:
        _require_profile(policy, "lab marks")
    normalized_credits = _normalize_credits(credits)
    _validate_range(ca1, 30, "Lab CA1")
    _validate_range(ca2, 30, "Lab CA2")
    valid, error = validate_external_marks(external, "LAB")
    if external is not None and str(external).strip() and not valid:
        raise ValueError(error or "Invalid lab external marks.")
    _validate_range(legacy_internal, 60, "Legacy lab internal")

    if external is None or str(external).strip() == "":
        result = {
            "ca1": ca1, "ca2": ca2, "internal": None, "external": None,
            "total": None, "grade": None, "grade_point": None,
            "credits_registered": normalized_credits, "credits_earned": 0,
            "is_passed": None, "fail_reason": "External lab marks not entered",
        }
    elif ca1 is not None and str(ca1).strip() and ca2 is not None and str(ca2).strip():
        result = compute_lab_result(ca1, ca2, external, normalized_credits)
    elif legacy_internal is not None and str(legacy_internal).strip():
        internal = float(legacy_internal)
        ext = float(external)
        total = round(internal + ext, 2)
        passed = total >= 40
        grade, grade_point = get_grade(total) if passed else ("FF", 0)
        result = {
            "ca1": None, "ca2": None, "internal": round(internal, 2),
            "external": round(ext, 1), "total": total, "grade": grade,
            "grade_point": grade_point, "credits_registered": normalized_credits,
            "credits_earned": normalized_credits if passed else 0,
            "is_passed": passed,
            "fail_reason": None if passed else f"Total below 40 (got {total}/100)",
        }
    else:
        result = {
            "ca1": ca1, "ca2": ca2, "internal": None,
            "external": float(external), "total": None, "grade": None,
            "grade_point": None, "credits_registered": normalized_credits,
            "credits_earned": 0, "is_passed": None,
            "fail_reason": "CA1/CA2 marks not entered",
        }
    result.update({
        "formula_profile": LEGACY_PROFILE_ID,
        "source_reference": LEGACY_SOURCE,
        "calculation_authority": "official-policy" if authoritative else "provisional-preview",
    })
    return result


__all__ = [
    "LEGACY_PROFILE_ID", "LEGACY_SOURCE", "PolicyPendingError",
    "calculate_theory", "calculate_lab", "calculate_sgpa", "calculate_cgpa",
    "get_grade", "is_gradable_subject", "is_theory_internal_complete",
]
