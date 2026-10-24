"""Validation and comparison helpers for versioned syllabus-derived curricula."""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any


COURSE_TYPES = {"theory", "lab", "practical", "project", "elective", "seminar", "internship", "audit", "other"}
SUBJECT_CODE_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./_-")


def normalize_subject_code(value: str) -> str:
    return "".join((value or "").upper().split())


def parse_assessment_structure(raw: str | dict | None) -> tuple[dict | None, str | None]:
    if isinstance(raw, dict):
        value = raw
    else:
        try:
            value = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return None, "Assessment structure must be valid JSON."
    if not isinstance(value, dict):
        return None, "Assessment structure JSON must be an object."
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return None, "Assessment structure contains values that cannot be stored safely."
    return value, None


def validate_assessment_structure(structure: dict) -> list[str]:
    errors: list[str] = []
    components = structure.get("components", [])
    if not isinstance(components, list):
        return ["Assessment structure 'components' must be a list."]

    if not components:
        reason = structure.get("not_applicable_reason")
        if isinstance(reason, str) and len(reason.strip()) >= 10:
            return []
        return ["Add at least one assessment component, or explain why assessment maxima are not applicable (10+ characters)."]

    component_names = set()
    for index, component in enumerate(components, start=1):
        if not isinstance(component, dict):
            errors.append(f"Assessment component {index} must be an object with name and max_marks.")
            continue
        name = str(component.get("name", "")).strip()
        if not name:
            errors.append(f"Assessment component {index} needs a name.")
        elif name.casefold() in component_names:
            errors.append(f"Assessment component name '{name}' is duplicated.")
        else:
            component_names.add(name.casefold())
        try:
            maximum = Decimal(str(component.get("max_marks", "")))
        except (InvalidOperation, ValueError):
            maximum = Decimal("NaN")
        if not maximum.is_finite() or maximum <= 0 or maximum > 10000:
            errors.append(f"Assessment component {index} max_marks must be greater than 0 and no greater than 10000.")
        if "minimum_marks" in component and component.get("minimum_marks") not in (None, ""):
            try:
                minimum = Decimal(str(component["minimum_marks"]))
            except (InvalidOperation, ValueError):
                minimum = Decimal("NaN")
            if not minimum.is_finite() or minimum < 0 or (maximum.is_finite() and minimum > maximum):
                errors.append(f"Assessment component {index} minimum_marks must be between 0 and max_marks.")

    if "total_max" in structure and structure.get("total_max") not in (None, ""):
        try:
            total_max = Decimal(str(structure["total_max"]))
        except (InvalidOperation, ValueError):
            total_max = Decimal("NaN")
        if not total_max.is_finite() or total_max <= 0 or total_max > 10000:
            errors.append("Assessment structure total_max must be greater than 0 and no greater than 10000.")
    return errors


def subject_readiness_errors(subject: Any) -> list[str]:
    errors: list[str] = []
    if not subject.subject_code or len(subject.subject_code.strip()) < 2:
        errors.append("subject code is missing")
    if not subject.title or len(subject.title.strip()) < 3:
        errors.append("title is missing")
    if not 1 <= int(subject.semester_number or 0) <= 12:
        errors.append("semester must be between 1 and 12")
    if subject.credits is None:
        errors.append("credits need to be confirmed")
    else:
        try:
            credits = Decimal(str(subject.credits))
        except (InvalidOperation, ValueError):
            credits = Decimal("NaN")
        if not credits.is_finite() or credits < 0 or credits > 999.99:
            errors.append("credits must be a valid number from 0 to 999.99")
    if subject.course_type not in COURSE_TYPES:
        errors.append("course type is invalid")
    if subject.review_status != "verified":
        errors.append("subject has not been verified")
    structure = subject.assessment_structure or {}
    errors.extend(validate_assessment_structure(structure))
    return errors


def curriculum_readiness_errors(version: Any) -> list[str]:
    if not version.subjects:
        return ["Add at least one subject before submitting this curriculum for review."]
    errors = []
    for subject in version.subjects:
        subject_errors = subject_readiness_errors(subject)
        if subject_errors:
            errors.append(f"{subject.subject_code or 'Uncoded subject'} (Semester {subject.semester_number}): " + "; ".join(subject_errors))
    return errors


def diff_curriculum_versions(previous: Any, current: Any) -> dict[str, list[dict[str, Any]]]:
    """Compare version subjects by semester + normalized code without mutating either."""
    def subject_map(version):
        result = {}
        for subject in version.subjects:
            key = (int(subject.semester_number), normalize_subject_code(subject.subject_code))
            result[key] = subject
        return result

    old_map, new_map = subject_map(previous), subject_map(current)
    added, removed, changed, unchanged = [], [], [], []
    for key in sorted(set(old_map) | set(new_map), key=lambda item: (item[0], item[1])):
        old, new = old_map.get(key), new_map.get(key)
        if old is None:
            added.append({"subject": new, "changes": []})
        elif new is None:
            removed.append({"subject": old, "changes": []})
        else:
            changed_fields = []
            for label, old_value, new_value in [
                ("Title", old.title, new.title),
                ("Credits", str(old.credits) if old.credits is not None else None, str(new.credits) if new.credits is not None else None),
                ("Course type", old.course_type, new.course_type),
                ("Assessment structure", old.assessment_structure or {}, new.assessment_structure or {}),
            ]:
                if old_value != new_value:
                    changed_fields.append({"field": label, "before": old_value, "after": new_value})
            if changed_fields:
                changed.append({"subject": new, "previous": old, "changes": changed_fields})
            else:
                unchanged.append({"subject": new, "previous": old, "changes": []})
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}
