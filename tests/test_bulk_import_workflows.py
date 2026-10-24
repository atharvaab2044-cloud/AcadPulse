"""Integration tests for preview-before-commit spreadsheet workflows.

These tests require the Flask/SQLAlchemy dependencies in requirements.txt.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO
import re

from app.extensions import db
from app.models import (
    AcademicProgram, Assessment, AssessmentMark, AttendanceRecord, AttendanceSession,
    AuditEvent, CurriculumScheme, CurriculumSubject, CurriculumVersion,
    Student, StudentEnrollment, User,
)

PASSWORD = "Bulk-Import-Test-Password-123!"


def _seed_case(app):
    with app.app_context():
        user = User(email="bulk-admin@example.test", full_name="Bulk Test Admin", role="admin")
        user.set_password(PASSWORD)
        program = AcademicProgram(code="BULKECE", name="Bulk Import Test Programme")
        scheme = CurriculumScheme(code="BULK_NEP_MIS", display_name="NEP/MIS")
        db.session.add_all([user, program, scheme])
        db.session.flush()
        version = CurriculumVersion(version_code="BULK_2026", scheme_id=scheme.id, program_id=program.id,
                                    effective_cohort_year=2026, status="active")
        db.session.add(version)
        db.session.flush()
        subject = CurriculumSubject(curriculum_version_id=version.id, subject_code="BULK101", title="Bulk Test Subject",
            semester_number=1, credits=Decimal("4"), course_type="theory",
            assessment_structure={"components": [{"name": "CT1", "max_marks": 10}]}, review_status="verified")
        student = Student(student_code="AP-BULK-TEST-0001", full_name="Fictional Bulk Student")
        db.session.add_all([subject, student])
        db.session.flush()
        enrollment = StudentEnrollment(student_id=student.id, program_id=program.id, curriculum_version_id=version.id,
            academic_year="2026-27", semester_number=1, roll_number="07", division="A", practical_batch="A1",
            enrollment_type="regular", enrollment_status="enrolled", admission_year=2026, curriculum_cohort_year=2026)
        db.session.add(enrollment)
        db.session.flush()
        assessment = Assessment(curriculum_subject_id=subject.id, academic_year="2026-27", semester_number=1,
            assessment_type="ct1", title="CT1 Bulk Test", max_marks=Decimal("10"), created_by_id=user.id, status="draft")
        session = AttendanceSession(curriculum_subject_id=subject.id, academic_year="2026-27", semester_number=1,
            held_on=date(2026, 9, 1), division="A", practical_batch="A1", topic="Bulk import test", created_by_id=user.id)
        db.session.add_all([assessment, session])
        db.session.commit()
        return {"assessment_id": assessment.id, "session_id": session.id, "enrollment_id": enrollment.id}


def _login(client):
    return client.post("/login", data={"email": "bulk-admin@example.test", "password": PASSWORD}, follow_redirects=True)


def _token_from(response, workflow, target_id):
    pattern = rf"/{workflow}/(?:assessments|sessions)/{target_id}/import-confirm/([a-f0-9]{{40}})"
    match = re.search(pattern, response.get_data(as_text=True))
    assert match, "Expected the preview screen to include its scoped confirm form."
    return match.group(1)


def test_coursework_csv_preview_then_atomic_commit(client, app):
    ids = _seed_case(app)
    _login(client)
    payload = b"student_code,marks,status\nAP-BULK-TEST-0001,8.5,present\n"
    preview = client.post(f"/coursework/assessments/{ids['assessment_id']}/import-preview",
        data={"import_file": (BytesIO(payload), "marks.csv")}, content_type="multipart/form-data")
    assert preview.status_code == 200
    assert b"No records have been changed yet" in preview.data
    token = _token_from(preview, "coursework", ids["assessment_id"])
    committed = client.post(f"/coursework/assessments/{ids['assessment_id']}/import-confirm/{token}", follow_redirects=True)
    assert committed.status_code == 200
    with app.app_context():
        mark = db.session.scalar(db.select(AssessmentMark).where(AssessmentMark.assessment_id == ids["assessment_id"]))
        assert mark is not None
        assert Decimal(mark.marks_obtained) == Decimal("8.5")
        assert mark.status == "present"
        assert db.session.scalar(db.select(AuditEvent.id).where(AuditEvent.action == "coursework_spreadsheet_import_committed")) is not None


def test_attendance_csv_preview_then_atomic_commit(client, app):
    ids = _seed_case(app)
    _login(client)
    payload = b"student_code,status\nAP-BULK-TEST-0001,present\n"
    preview = client.post(f"/attendance/sessions/{ids['session_id']}/import-preview",
        data={"import_file": (BytesIO(payload), "attendance.csv")}, content_type="multipart/form-data")
    assert preview.status_code == 200
    assert b"No records have been changed yet" in preview.data
    token = _token_from(preview, "attendance", ids["session_id"])
    committed = client.post(f"/attendance/sessions/{ids['session_id']}/import-confirm/{token}", follow_redirects=True)
    assert committed.status_code == 200
    with app.app_context():
        record = db.session.scalar(db.select(AttendanceRecord).where(AttendanceRecord.session_id == ids["session_id"]))
        assert record is not None
        assert record.status == "present"
        assert db.session.scalar(db.select(AuditEvent.id).where(AuditEvent.action == "attendance_spreadsheet_import_committed")) is not None


def test_import_preview_token_cannot_be_reused_for_another_assessment(client, app):
    ids = _seed_case(app)
    _login(client)
    payload = b"student_code,marks,status\nAP-BULK-TEST-0001,8,present\n"
    preview = client.post(f"/coursework/assessments/{ids['assessment_id']}/import-preview",
        data={"import_file": (BytesIO(payload), "marks.csv")}, content_type="multipart/form-data")
    token = _token_from(preview, "coursework", ids["assessment_id"])
    # There is only one assessment in this fixture; a different target ID must be rejected/not applied.
    response = client.post(f"/coursework/assessments/99999/import-confirm/{token}", follow_redirects=True)
    assert response.status_code in {200, 404}
    with app.app_context():
        assert db.session.scalar(db.select(AssessmentMark.id)) is None
