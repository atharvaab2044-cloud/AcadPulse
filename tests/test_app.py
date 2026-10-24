from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import AcademicProgram, CurriculumScheme, CurriculumVersion, Student, StudentEnrollment, User


def _admin():
    user = User(email="admin@example.test", full_name="Test Admin", role="admin")
    user.set_password("Test-Password-123!")
    return user


def test_dashboard_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_admin_can_log_in_and_open_dashboard(client, app):
    with app.app_context():
        db.session.add(_admin())
        db.session.commit()
    response = client.post("/login", data={"email": "admin@example.test", "password": "Test-Password-123!"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"ACADEMIC OPERATIONS" in response.data
    assert b"Student records" in response.data


def test_student_identity_and_term_roll_history_are_separate(app):
    with app.app_context():
        user = _admin()
        program = AcademicProgram(code="ECE", name="Electronics and Computer Engineering")
        scheme = CurriculumScheme(code="NEP_MIS", display_name="NEP/MIS")
        db.session.add_all([user, program, scheme])
        db.session.flush()
        version = CurriculumVersion(version_code="NEP_MIS_2026", scheme_id=scheme.id, program_id=program.id, effective_cohort_year=2026, status="active")
        student = Student(student_code="ECE-2026-0001", full_name="Fictional Student")
        db.session.add_all([version, student])
        db.session.flush()
        first = StudentEnrollment(student_id=student.id, program_id=program.id, curriculum_version_id=version.id, academic_year="2026-27", semester_number=1, roll_number="18", division="A", admission_year=2026, curriculum_cohort_year=2026)
        second = StudentEnrollment(student_id=student.id, program_id=program.id, curriculum_version_id=version.id, academic_year="2027-28", semester_number=3, roll_number="42", division="B", admission_year=2026, curriculum_cohort_year=2026)
        db.session.add_all([first, second])
        db.session.commit()
        rows = db.session.execute(db.select(StudentEnrollment).where(StudentEnrollment.student_id == student.id).order_by(StudentEnrollment.academic_year)).scalars().all()
        assert [r.roll_number for r in rows] == ["18", "42"]
        assert rows[0].student_id == rows[1].student_id


def test_duplicate_enrollment_same_term_is_rejected(app):
    with app.app_context():
        program = AcademicProgram(code="ECE", name="Electronics and Computer Engineering")
        student = Student(student_code="ECE-2026-0002", full_name="Another Fictional Student")
        db.session.add_all([program, student])
        db.session.flush()
        row = StudentEnrollment(student_id=student.id, program_id=program.id, academic_year="2026-27", semester_number=1)
        db.session.add(row)
        db.session.commit()
        duplicate = StudentEnrollment(student_id=student.id, program_id=program.id, academic_year="2026-27", semester_number=1)
        db.session.add(duplicate)
        try:
            db.session.commit()
            assert False, "Expected unique enrollment constraint to reject duplicate"
        except IntegrityError:
            db.session.rollback()
