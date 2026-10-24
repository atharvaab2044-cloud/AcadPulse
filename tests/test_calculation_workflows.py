"""Integration checks for snapshot gating and effective-attempt policy workflow."""
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models import (
    AcademicPolicy, AcademicProgram, AuditEvent, CalculationSnapshot, CurriculumScheme,
    CurriculumSubject, CurriculumVersion, ExamAttempt, Student, StudentEnrollment, User,
)
from app.services.legacy_mark_service import LEGACY_PROFILE_ID


def _admin():
    user = User(email="calc-admin@example.test", full_name="Calculation Admin", role="admin")
    user.set_password("Strong-Test-Password-123!")
    return user


def _fixture_records():
    user = _admin()
    program = AcademicProgram(code="ECX", name="Electronics and Computer Engineering")
    scheme = CurriculumScheme(code="CALC_TEST", display_name="Calculation Test Scheme")
    db.session.add_all([user, program, scheme])
    db.session.flush()
    version = CurriculumVersion(version_code="CALC_TEST_2026", scheme_id=scheme.id, program_id=program.id,
                                effective_cohort_year=2026, status="active")
    student = Student(student_code="AP-CALC-TEST-0001", full_name="Fictional Test Student")
    db.session.add_all([version, student])
    db.session.flush()
    enrollment = StudentEnrollment(student_id=student.id, program_id=program.id,
        curriculum_version_id=version.id, academic_year="2026-27", semester_number=1,
        roll_number="1", division="A", enrollment_type="regular", enrollment_status="enrolled",
        admission_year=2026, curriculum_cohort_year=2026)
    subject = CurriculumSubject(curriculum_version_id=version.id, subject_code="CALC101", title="Calculation Test Subject",
        semester_number=1, credits=Decimal("4"), course_type="theory", assessment_structure={}, review_status="verified")
    db.session.add_all([enrollment, subject])
    db.session.flush()
    return user, program, version, student, enrollment, subject


def _active_grading_policy(user, program, version):
    policy = AcademicPolicy(policy_code="CALC_GRADE_2026", policy_type="grading", program_id=program.id,
        curriculum_version_id=version.id, effective_from_cohort_year=2026, status="active",
        rules_json={"calculation_profile": LEGACY_PROFILE_ID}, source_reference="Test policy source reference",
        approved_by_id=user.id, approved_at=datetime.now(timezone.utc),
        approval_note="Approved for automated integration testing only.")
    db.session.add(policy)
    db.session.flush()
    return policy


def _attempt(user, student, enrollment, subject, *, effective=True, status="pass", attempt_type="regular"):
    attempt = ExamAttempt(student_id=student.id, enrollment_id=enrollment.id, curriculum_subject_id=subject.id,
        academic_year=enrollment.academic_year, semester_number=enrollment.semester_number,
        attempt_type=attempt_type, total_marks=Decimal("80"), official_grade="BB", official_grade_point=Decimal("8.0"),
        official_status=status, effective_for_calculation=effective, verification_status="verified",
        verified_by_id=user.id, verified_at=datetime.now(timezone.utc), source_note="Synthetic test data.")
    db.session.add(attempt)
    db.session.flush()
    return attempt


def _login(client):
    return client.post("/login", data={"email": "calc-admin@example.test", "password": "Strong-Test-Password-123!"}, follow_redirects=True)


def test_calculations_menu_and_index_are_registered(client, app):
    with app.app_context():
        db.session.add(_admin())
        db.session.commit()
    response = _login(client)
    assert response.status_code == 200
    page = client.get("/calculations")
    assert page.status_code == 200
    assert b"Calculations & promotion" in page.data


def test_sgpa_snapshot_must_be_approved_before_cgpa_uses_it(client, app):
    with app.app_context():
        user, program, version, student, enrollment, subject = _fixture_records()
        _active_grading_policy(user, program, version)
        attempt = _attempt(user, student, enrollment, subject)
        db.session.commit()
        student_id, enrollment_id, attempt_id = student.id, enrollment.id, attempt.id
    _login(client)
    page = client.get(f"/calculations/enrollments/{enrollment_id}")
    assert page.status_code == 200
    response = client.post(f"/calculations/enrollments/{enrollment_id}/snapshot", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        snap = db.session.scalar(db.select(CalculationSnapshot).where(CalculationSnapshot.enrollment_id == enrollment_id,
            CalculationSnapshot.calculation_type == "sgpa"))
        assert snap is not None
        assert snap.status == "calculated"
        assert Decimal(str(snap.value_numeric)) == Decimal("8.00")
        snapshot_id = snap.id
    response = client.post(f"/calculations/snapshots/{snapshot_id}/approve", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        snap = db.session.get(CalculationSnapshot, snapshot_id)
        assert snap.status == "approved"
        assert snap.approved_by_id is not None
    response = client.get(f"/calculations/students/{student_id}/cgpa")
    assert response.status_code == 200
    assert b"8.00" in response.data


def test_effective_attempt_requires_active_policy_and_audit_reason(client, app):
    with app.app_context():
        user, program, version, student, enrollment, subject = _fixture_records()
        attempt = _attempt(user, student, enrollment, subject, effective=False)
        policy = AcademicPolicy(policy_code="CALC_SUPP_2026", policy_type="supplementary", program_id=program.id,
            curriculum_version_id=version.id, effective_from_cohort_year=2026, status="active",
            rules_json={"effective_attempt_selection": "admin_selected"}, source_reference="Test supplementary source",
            approved_by_id=user.id, approved_at=datetime.now(timezone.utc),
            approval_note="Approved for automated integration testing only.")
        db.session.add(policy)
        db.session.commit()
        student_id, attempt_id = student.id, attempt.id
    _login(client)
    response = client.post(f"/university-results/attempts/{attempt_id}/set-effective", data={
        "reason": "Test administrator explicitly reviewed the official attempt and selected it for the term calculation."
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        attempt = db.session.get(ExamAttempt, attempt_id)
        assert attempt.effective_for_calculation is True
        assert db.session.scalar(db.select(AuditEvent.id).where(AuditEvent.action == "exam_attempt_selected_effective")) is not None


def test_promotion_without_active_policy_is_pending_and_does_not_change_enrollment(client, app):
    with app.app_context():
        user, program, version, student, enrollment, subject = _fixture_records()
        db.session.commit()
        enrollment_id = enrollment.id
        original_status = enrollment.enrollment_status
    _login(client)
    response = client.get(f"/calculations/enrollments/{enrollment_id}/promotion")
    assert response.status_code == 200
    assert b"Policy Pending" in response.data or b"policy pending" in response.data.lower()
    response = client.post(f"/calculations/enrollments/{enrollment_id}/promotion/snapshot", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        enrollment = db.session.get(StudentEnrollment, enrollment_id)
        snap = db.session.scalar(db.select(CalculationSnapshot).where(CalculationSnapshot.enrollment_id == enrollment_id,
            CalculationSnapshot.calculation_type == "promotion"))
        assert enrollment.enrollment_status == original_status
        assert snap.status == "policy_pending"
