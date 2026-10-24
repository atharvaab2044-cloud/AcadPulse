"""Integration tests for student identity and semester enrollment workflows."""
from datetime import date

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    AcademicProgram,
    AuditEvent,
    CurriculumScheme,
    CurriculumVersion,
    Student,
    StudentEnrollment,
    StudentIdentifier,
    User,
)


def _create_user(role="admin", email=None):
    user = User(email=email or f"{role}@example.test", full_name=f"Test {role.title()}", role=role)
    user.set_password("Test-Password-123!")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, email, role_unused=None):
    return client.post("/login", data={"email": email, "password": "Test-Password-123!"}, follow_redirects=True)


def _create_program(code="ECE"):
    program = AcademicProgram(code=code, name="Electronics and Computer Engineering", degree="B.E.", duration_years=4)
    db.session.add(program)
    db.session.commit()
    return program


def _create_student(client, app, name="Aarav Example"):
    response = client.post("/students/new", data={"full_name": name}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        with db.session.no_autoflush:
            student = db.session.scalar(select(Student).where(Student.full_name == name))
        assert student is not None
        return student.id


def test_student_directory_opens_and_admin_can_create_profile(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    response = _login(client, "admin@example.test")
    assert response.status_code == 200
    assert b"Students" in client.get("/students").data

    student_id = _create_student(client, app)
    with app.app_context():
        student = db.session.get(Student, student_id)
        assert student.student_code.startswith(f"AP-{date.today().year}-")
        assert student.status == "active"
        assert student.identifiers == []
        assert db.session.scalar(select(AuditEvent).where(AuditEvent.action == "student_created")) is not None


def test_faculty_cannot_create_student_or_program(client, app):
    with app.app_context():
        _create_user("faculty", "faculty@example.test")
    _login(client, "faculty@example.test")
    assert client.get("/students/new").status_code == 403
    assert client.post("/programs/new", data={"code": "ECE", "name": "ECE"}).status_code == 403


def test_prn_is_pending_until_explicit_verification(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    student_id = _create_student(client, app, "Diya Example")

    response = client.post(f"/students/{student_id}/identifiers/prn", data={"prn": "  PRN 2026 001 "}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        identifier = db.session.scalar(select(StudentIdentifier).where(StudentIdentifier.student_id == student_id))
        assert identifier.identifier_type == "prn"
        assert identifier.normalized_value == "PRN2026001"
        assert identifier.verification_status == "pending"
        identifier_id = identifier.id
    response = client.post(f"/students/{student_id}/identifiers/{identifier_id}/verify", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        identifier = db.session.get(StudentIdentifier, identifier_id)
        assert identifier.verification_status == "verified"
        assert identifier.verified_by_id is not None
        assert identifier.verified_at is not None


def test_duplicate_prn_cannot_be_attached_to_second_student(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    first_id = _create_student(client, app, "First Student")
    second_id = _create_student(client, app, "Second Student")
    client.post(f"/students/{first_id}/identifiers/prn", data={"prn": "PRN-UNIQUE-001"})
    client.post(f"/students/{second_id}/identifiers/prn", data={"prn": " prn-unique-001 "})
    with app.app_context():
        matches = db.session.execute(select(StudentIdentifier).where(StudentIdentifier.identifier_type == "prn")).scalars().all()
        assert len(matches) == 1
        assert matches[0].student_id == first_id


def test_program_can_be_created_and_duplicates_are_rejected(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    response = client.post("/programs/new", data={"code": "ece", "name": "Electronics and Computer Engineering", "degree": "B.E.", "duration_years": "4"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Programme ECE created" in response.data
    response = client.post("/programs/new", data={"code": "ECE", "name": "Duplicate programme", "degree": "B.E.", "duration_years": "4"}, follow_redirects=True)
    assert b"already exists" in response.data
    with app.app_context():
        assert db.session.scalar(select(AcademicProgram).where(AcademicProgram.code == "ECE")) is not None
        assert db.session.scalar(select(AcademicProgram.id).where(AcademicProgram.name == "Duplicate programme")) is None


def test_dse_initial_enrollment_requires_semester_three_and_keeps_actual_admission_year(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    with app.app_context():
        program = _create_program()
        program_id = program.id
    student_id = _create_student(client, app, "Kabir DSE Example")

    response = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2026-27", "semester_number": "1",
        "enrollment_type": "DSE", "enrollment_status": "enrolled", "admission_year": "2026",
        "curriculum_cohort_year": "2024", "roll_number": "43", "division": "A",
    })
    assert response.status_code == 400
    assert b"must start at Semester 3" in response.data
    with app.app_context():
        assert db.session.scalar(select(StudentEnrollment.id).where(StudentEnrollment.student_id == student_id)) is None

    response = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2026-27", "semester_number": "3",
        "enrollment_type": "DSE", "enrollment_status": "enrolled", "admission_year": "2026",
        "curriculum_cohort_year": "2024", "roll_number": "43", "division": "A", "practical_batch": "A2",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        enrollment = db.session.scalar(select(StudentEnrollment).where(StudentEnrollment.student_id == student_id))
        assert enrollment.semester_number == 3
        assert enrollment.admission_year == 2026
        assert enrollment.curriculum_cohort_year == 2024
        assert enrollment.roll_number == "43"
        assert enrollment.enrollment_type == "DSE"


def test_enrollment_history_preserves_changing_roll_number(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    with app.app_context():
        program = _create_program()
        program_id = program.id
    student_id = _create_student(client, app, "Mira Roll History")

    first = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2026-27", "semester_number": "1",
        "enrollment_type": "regular", "enrollment_status": "enrolled", "admission_year": "2026",
        "roll_number": "18", "division": "A", "practical_batch": "A1",
    }, follow_redirects=True)
    assert first.status_code == 200
    second = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2027-28", "semester_number": "3",
        "enrollment_type": "regular", "enrollment_status": "enrolled", "admission_year": "2026",
        "roll_number": "42", "division": "B", "practical_batch": "B1",
    }, follow_redirects=True)
    assert second.status_code == 200
    with app.app_context():
        rows = db.session.execute(select(StudentEnrollment).where(StudentEnrollment.student_id == student_id).order_by(StudentEnrollment.academic_year)).scalars().all()
        assert [(row.academic_year, row.semester_number, row.roll_number, row.division) for row in rows] == [
            ("2026-27", 1, "18", "A"), ("2027-28", 3, "42", "B")
        ]
        assert len({row.student_id for row in rows}) == 1


def test_wrong_program_curriculum_version_is_rejected(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    with app.app_context():
        program = _create_program("ECE")
        other = _create_program("CSE")
        scheme = CurriculumScheme(code="NEP_MIS", display_name="NEP/MIS")
        db.session.add(scheme)
        db.session.flush()
        version = CurriculumVersion(version_code="CSE-2026", scheme_id=scheme.id, program_id=other.id, effective_cohort_year=2026, status="active")
        db.session.add(version)
        db.session.commit()
        program_id, version_id = program.id, version.id
    student_id = _create_student(client, app, "Aarav Mismatch")

    response = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id), "curriculum_version_id": str(version_id),
        "academic_year": "2026-27", "semester_number": "1", "enrollment_type": "regular",
        "enrollment_status": "enrolled", "admission_year": "2026", "curriculum_cohort_year": "2026",
    })
    assert response.status_code == 400
    assert b"belongs to a different programme" in response.data
    with app.app_context():
        assert db.session.scalar(select(StudentEnrollment.id).where(StudentEnrollment.student_id == student_id)) is None


def test_duplicate_roll_in_same_program_term_and_division_is_rejected(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
        program = _create_program()
        program_id = program.id
    _login(client, "admin@example.test")
    first_id = _create_student(client, app, "Roll Owner One")
    second_id = _create_student(client, app, "Roll Owner Two")
    first_response = client.post(f"/students/{first_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2026-27", "semester_number": "1",
        "enrollment_type": "regular", "enrollment_status": "enrolled", "admission_year": "2026",
        "roll_number": "09", "division": "A",
    }, follow_redirects=True)
    assert first_response.status_code == 200
    second_response = client.post(f"/students/{second_id}/enrollments", data={
        "program_id": str(program_id), "academic_year": "2026-27", "semester_number": "1",
        "enrollment_type": "regular", "enrollment_status": "enrolled", "admission_year": "2026",
        "roll_number": "09", "division": "a",
    })
    assert second_response.status_code == 400
    assert b"already used in this programme, semester and division" in second_response.data
    with app.app_context():
        enrollments = db.session.execute(select(StudentEnrollment).where(
            StudentEnrollment.program_id == program_id,
            StudentEnrollment.academic_year == "2026-27",
            StudentEnrollment.semester_number == 1,
        )).scalars().all()
        assert len(enrollments) == 1
        assert enrollments[0].student_id == first_id


def test_new_student_redirects_to_first_enrollment_step(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
    _login(client, "admin@example.test")
    response = client.post("/students/new", data={"full_name": "Next Step Example"})
    assert response.status_code == 302
    assert response.location.endswith("#add-enrollment")


def test_invalid_enrollment_rerenders_form_with_validation_errors_not_server_error(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
        program_id = _create_program().id
    _login(client, "admin@example.test")
    student_id = _create_student(client, app, "Invalid Enrollment Example")

    response = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id),
        "academic_year": "not-a-year",
        "semester_number": "13",
        "enrollment_type": "regular",
        "enrollment_status": "enrolled",
        "admission_year": str(date.today().year),
    })
    assert response.status_code == 400
    assert b"Academic year must use the format" in response.data
    assert b"Semester must be between 1 and 12" in response.data
    assert b"Create first academic enrollment" in response.data


def test_existing_student_enrollment_form_keeps_admission_context_but_requires_semester_choice(client, app):
    with app.app_context():
        _create_user("admin", "admin@example.test")
        program_id = _create_program().id
    _login(client, "admin@example.test")
    student_id = _create_student(client, app, "Enrollment Defaults Example")
    year = date.today().year
    next_year = (year + 1) % 100
    academic_year = f"{year}-{next_year:02d}"
    response = client.post(f"/students/{student_id}/enrollments", data={
        "program_id": str(program_id),
        "academic_year": academic_year,
        "semester_number": "1",
        "enrollment_type": "regular",
        "enrollment_status": "enrolled",
        "admission_year": str(year),
        "curriculum_cohort_year": str(year),
        "roll_number": "17",
        "division": "A",
    })
    assert response.status_code == 302

    detail = client.get(f"/students/{student_id}")
    assert detail.status_code == 200
    html = detail.data.decode("utf-8")
    assert f'value="{program_id}" selected' in html
    assert f'value="{year}"' in html
    assert '<option value="" selected>Choose semester</option>' in html
    assert 'id="add-enrollment"' in html
    assert 'id="enrollment-history"' in html
