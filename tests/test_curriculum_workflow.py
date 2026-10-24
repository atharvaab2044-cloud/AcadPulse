"""Pure validation and comparison tests for syllabus-derived curriculum versions."""
from types import SimpleNamespace

from app.services.curriculum_workflow import (
    curriculum_readiness_errors,
    diff_curriculum_versions,
    validate_assessment_structure,
)


def subject(code="BSC101", title="Engineering Mathematics", semester=1, credits="4.00", course_type="theory", structure=None, review_status="verified"):
    return SimpleNamespace(
        subject_code=code,
        title=title,
        semester_number=semester,
        credits=credits,
        course_type=course_type,
        assessment_structure=structure or {"components": [{"name": "Internal", "max_marks": 40}, {"name": "External", "max_marks": 60}]},
        review_status=review_status,
    )


def version(*subjects):
    return SimpleNamespace(subjects=list(subjects))


def test_assessment_structure_requires_components_or_explicit_reason():
    assert validate_assessment_structure({"components": []})
    assert validate_assessment_structure({"components": [], "not_applicable_reason": "Audit-only subject, marks not assigned in the approved scheme."}) == []
    assert validate_assessment_structure({"components": [{"name": "Exam", "max_marks": 60}]}) == []
    assert validate_assessment_structure({"components": [{"name": "Exam", "max_marks": 0}]})


def test_readiness_blocks_unverified_or_incomplete_subjects():
    errors = curriculum_readiness_errors(version(subject(review_status="review_required", structure={"components": []})))
    assert errors
    assert any("has not been verified" in error for error in errors)
    assert any("assessment component" in error for error in errors)


def test_readiness_accepts_subject_with_reviewed_assessment_definition():
    assert curriculum_readiness_errors(version(subject())) == []


def test_revision_diff_finds_added_changed_removed_and_unchanged():
    previous = version(
        subject("BSC101", "Engineering Mathematics", 1, "4.00"),
        subject("BSC102", "Engineering Physics", 1, "3.00"),
        subject("ESC201", "Programming Fundamentals", 2, "3.00"),
    )
    current = version(
        subject("BSC101", "Engineering Mathematics I", 1, "4.00"),
        subject("ESC201", "Programming Fundamentals", 2, "3.00"),
        subject("ESC202", "Programming Lab", 2, "1.00", "lab"),
    )
    diff = diff_curriculum_versions(previous, current)
    assert [item["subject"].subject_code for item in diff["added"]] == ["ESC202"]
    assert [item["subject"].subject_code for item in diff["removed"]] == ["BSC102"]
    assert [item["subject"].subject_code for item in diff["changed"]] == ["BSC101"]
    assert [item["subject"].subject_code for item in diff["unchanged"]] == ["ESC201"]
