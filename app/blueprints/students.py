"""Student identity, PRN verification, and semester enrollment workflows."""
from datetime import date
import re
from secrets import token_hex

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    AcademicProgram,
    Assessment, AssessmentMark, AttendanceRecord, AttendanceSession, ExamAttempt, StudentElectiveSelection,
    AuditEvent,
    CurriculumSubject, CurriculumVersion,
    Student,
    StudentEnrollment,
    StudentIdentifier,
    utcnow,
)
from ..services.security import roles_required

bp = Blueprint("students", __name__)

STUDENT_STATUSES = {"active", "inactive", "graduated", "withdrawn", "deceased"}
ENROLLMENT_TYPES = {"regular", "DSE", "transfer", "re_admit"}
ENROLLMENT_STATUSES = {"enrolled", "detained", "withdrawn", "completed", "cancelled"}
CURRICULUM_USABLE_STATUSES = {"approved", "active"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _audit(action, entity_type, entity_id, *, before=None, after=None, reason=None):
    """Add a minimized audit event to the current transaction."""
    db.session.add(AuditEvent(
        actor_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=before,
        after_json=after,
        reason=reason,
    ))


def _display_name(value):
    return " ".join((value or "").split())


def _parse_optional_date(value, label):
    value = (value or "").strip()
    if not value:
        return None, None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, f"{label} must be a valid date."
    if parsed > date.today() and label == "Date of birth":
        return None, "Date of birth cannot be in the future."
    return parsed, None


def _parse_positive_id(value, label, *, required=False):
    raw = (value or "").strip()
    if not raw:
        return (None, f"{label} is required.") if required else (None, None)
    try:
        parsed = int(raw)
    except ValueError:
        return None, f"{label} must be a valid selection."
    if parsed < 1:
        return None, f"{label} must be a valid selection."
    return parsed, None


def _parse_year(value, label, *, required=False):
    raw = (value or "").strip()
    if not raw:
        return (None, f"{label} is required.") if required else (None, None)
    try:
        year = int(raw)
    except ValueError:
        return None, f"{label} must be a whole year."
    if not 1990 <= year <= 2200:
        return None, f"{label} must be between 1990 and 2200."
    return year, None


def _validate_academic_year(value):
    value = (value or "").strip()
    match = ACADEMIC_YEAR_RE.fullmatch(value)
    if not match:
        return None, "Academic year must use the format YYYY-YY, for example 2026-27."
    start = int(match.group(1))
    ending_two_digits = int(match.group(2))
    if not 1990 <= start <= 2199 or ending_two_digits != (start + 1) % 100:
        return None, "Academic year must describe two consecutive years, for example 2026-27."
    return value, None


def _new_student_code():
    """Create a human-readable public code; database student.id remains the identity key."""
    year = date.today().year
    for _ in range(20):
        candidate = f"AP-{year}-{token_hex(3).upper()}"
        exists = db.session.scalar(select(Student.id).where(Student.student_code == candidate))
        if not exists:
            return candidate
    raise RuntimeError("Could not generate a unique student code. Please retry.")


def _active_prns(student):
    return [
        identifier for identifier in student.identifiers
        if identifier.identifier_type == "prn" and identifier.verification_status != "rejected"
    ]


def _default_enrollment_values(enrollments, *, current_academic_year, current_year):
    """Defaults for the next enrollment form without guessing progression.

    For a new student, suggest the first regular semester. For an existing
    student, carry forward stable academic context (programme, admission year,
    cohort and latest known curriculum), but leave semester/roll/division/batch
    blank so staff must explicitly record the new term's values.
    """
    defaults = {
        "program_id": "",
        "curriculum_version_id": "",
        "academic_year": current_academic_year,
        "semester_number": "1",
        "enrollment_type": "regular",
        "enrollment_status": "enrolled",
        "admission_year": str(current_year),
        "curriculum_cohort_year": "",
        "roll_number": "",
        "division": "",
        "practical_batch": "",
        "enrolled_at": "",
        "notes": "",
    }
    if not enrollments:
        return defaults

    # The query used by _render_student_detail is ordered newest to oldest.
    latest = enrollments[0]
    oldest_with_admission_year = next(
        (row for row in reversed(enrollments) if row.admission_year is not None),
        None,
    )
    latest_curriculum = next(
        (row for row in enrollments if row.curriculum_version_id is not None),
        None,
    )
    defaults.update({
        "program_id": str(latest.program_id),
        "curriculum_version_id": str(latest_curriculum.curriculum_version_id) if latest_curriculum else "",
        "semester_number": "",  # Never infer promotion or the next eligible semester.
        "enrollment_type": latest.enrollment_type,
        "admission_year": str(oldest_with_admission_year.admission_year) if oldest_with_admission_year else str(current_year),
        "curriculum_cohort_year": str(latest.curriculum_cohort_year) if latest.curriculum_cohort_year is not None else "",
    })
    return defaults


def _render_student_detail(student, enrollment_form_data=None, status=200):
    programs = db.session.execute(
        select(AcademicProgram).where(AcademicProgram.is_active.is_(True)).order_by(AcademicProgram.name.asc())
    ).scalars().all()
    versions = db.session.execute(
        select(CurriculumVersion)
        .where(CurriculumVersion.status.in_(CURRICULUM_USABLE_STATUSES))
        .order_by(CurriculumVersion.effective_cohort_year.desc(), CurriculumVersion.version_code.asc())
    ).scalars().all()
    enrollments = db.session.execute(
        select(StudentEnrollment)
        .where(StudentEnrollment.student_id == student.id)
        .order_by(StudentEnrollment.academic_year.desc(), StudentEnrollment.semester_number.desc(), StudentEnrollment.id.desc())
    ).scalars().all()
    default_enrollment_values = _default_enrollment_values(
        enrollments,
        current_academic_year=current_app.config["CURRENT_ACADEMIC_YEAR"],
        current_year=date.today().year,
    )
    coursework_rows = db.session.execute(
        select(AssessmentMark, Assessment, StudentEnrollment, CurriculumSubject)
        .join(Assessment, Assessment.id == AssessmentMark.assessment_id)
        .join(StudentEnrollment, StudentEnrollment.id == AssessmentMark.enrollment_id)
        .join(CurriculumSubject, CurriculumSubject.id == Assessment.curriculum_subject_id)
        .where(StudentEnrollment.student_id == student.id)
        .order_by(Assessment.academic_year.desc(), Assessment.semester_number.desc(), Assessment.created_at.desc())
        .limit(150)
    ).all()
    attendance_rows = db.session.execute(
        select(AttendanceSession, AttendanceRecord, StudentEnrollment)
        .join(AttendanceRecord, AttendanceRecord.session_id == AttendanceSession.id)
        .join(StudentEnrollment, StudentEnrollment.id == AttendanceRecord.enrollment_id)
        .where(StudentEnrollment.student_id == student.id)
        .order_by(AttendanceSession.held_on.desc(), AttendanceSession.id.desc())
        .limit(150)
    ).all()
    exam_attempts = db.session.execute(
        select(ExamAttempt).options(selectinload(ExamAttempt.subject), selectinload(ExamAttempt.enrollment))
        .where(ExamAttempt.student_id == student.id)
        .order_by(ExamAttempt.academic_year.desc(), ExamAttempt.semester_number.desc(), ExamAttempt.created_at.desc())
        .limit(150)
    ).scalars().all()
    elective_selections = db.session.execute(
        select(StudentElectiveSelection).join(StudentEnrollment)
        .options(selectinload(StudentElectiveSelection.subject), selectinload(StudentElectiveSelection.elective_group),
                 selectinload(StudentElectiveSelection.enrollment))
        .where(StudentEnrollment.student_id == student.id)
        .order_by(StudentElectiveSelection.selected_at.desc())
    ).scalars().all()
    return render_template(
        "student_detail.html",
        page_title=student.full_name,
        student=student,
        enrollments=enrollments,
        programs=programs,
        curriculum_versions=versions,
        active_prns=_active_prns(student),
        enrollment_form_data=enrollment_form_data or {},
        default_enrollment_values=default_enrollment_values,
        current_academic_year=current_app.config["CURRENT_ACADEMIC_YEAR"],
        current_year=date.today().year,
        coursework_rows=coursework_rows,
        attendance_rows=attendance_rows,
        exam_attempts=exam_attempts,
        elective_selections=elective_selections,
    ), status


@bp.get("/students")
@login_required
def index():
    search = request.args.get("q", "").strip()
    query = select(Student).options(
        selectinload(Student.identifiers), selectinload(Student.enrollments)
    ).order_by(Student.full_name.asc(), Student.id.asc())
    if search:
        like = f"%{search}%"
        identifier_student_ids = select(StudentIdentifier.student_id).where(
            StudentIdentifier.normalized_value.ilike(like)
        )
        roll_student_ids = select(StudentEnrollment.student_id).where(
            StudentEnrollment.roll_number.ilike(like)
        )
        query = query.where(or_(
            Student.full_name.ilike(like),
            Student.student_code.ilike(like),
            Student.id.in_(identifier_student_ids),
            Student.id.in_(roll_student_ids),
        ))
    page = request.args.get("page", 1, type=int)
    students_page = db.paginate(query, page=max(page, 1), per_page=20, error_out=False)
    return render_template("students.html", page_title="Students", students_page=students_page, search=search)


@bp.route("/students/new", methods=["GET", "POST"])
@roles_required("admin")
def create():
    form_data = request.form if request.method == "POST" else {}
    if request.method == "POST":
        full_name = _display_name(request.form.get("full_name"))
        email = (request.form.get("email") or "").strip().lower() or None
        phone = (request.form.get("phone") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None
        dob, dob_error = _parse_optional_date(request.form.get("date_of_birth"), "Date of birth")
        errors = []
        if not full_name:
            errors.append("Full name is required.")
        elif len(full_name) > 180:
            errors.append("Full name must be 180 characters or fewer.")
        if email and (len(email) > 254 or not EMAIL_RE.fullmatch(email)):
            errors.append("Enter a valid email address no longer than 254 characters.")
        if phone and len(phone) > 32:
            errors.append("Phone number must be 32 characters or fewer.")
        if notes and len(notes) > 10000:
            errors.append("Notes must be 10,000 characters or fewer.")
        if dob_error:
            errors.append(dob_error)
        for error in errors:
            flash(error, "error")
        if errors:
            return render_template("student_form.html", page_title="Add student", form_data=form_data), 400

        student = Student(
            student_code=_new_student_code(),
            full_name=full_name,
            email=email,
            phone=phone,
            date_of_birth=dob,
            status="active",
            notes=notes,
        )
        db.session.add(student)
        try:
            db.session.flush()
            _audit(
                "student_created", "student", student.id,
                after={"student_code": student.student_code, "status": student.status},
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The student could not be saved due to a duplicate or conflicting record. Please retry.", "error")
            return render_template("student_form.html", page_title="Add student", form_data=form_data), 409
        flash(f"Student profile created. Student code: {student.student_code}. Add a PRN later after it is issued and verified.", "success")
        return redirect(url_for("students.detail", student_id=student.id, _anchor="add-enrollment"))
    return render_template("student_form.html", page_title="Add student", form_data=form_data)


@bp.get("/students/<int:student_id>")
@login_required
def detail(student_id):
    student = db.get_or_404(Student, student_id)
    return _render_student_detail(student)


@bp.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit(student_id):
    student = db.get_or_404(Student, student_id)
    form_data = request.form if request.method == "POST" else {
        "full_name": student.full_name,
        "email": student.email or "",
        "phone": student.phone or "",
        "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else "",
        "status": student.status,
        "notes": student.notes or "",
    }
    if request.method == "POST":
        full_name = _display_name(request.form.get("full_name"))
        email = (request.form.get("email") or "").strip().lower() or None
        phone = (request.form.get("phone") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None
        status = (request.form.get("status") or "").strip().lower()
        dob, dob_error = _parse_optional_date(request.form.get("date_of_birth"), "Date of birth")
        errors = []
        if not full_name:
            errors.append("Full name is required.")
        elif len(full_name) > 180:
            errors.append("Full name must be 180 characters or fewer.")
        if email and (len(email) > 254 or not EMAIL_RE.fullmatch(email)):
            errors.append("Enter a valid email address no longer than 254 characters.")
        if phone and len(phone) > 32:
            errors.append("Phone number must be 32 characters or fewer.")
        if notes and len(notes) > 10000:
            errors.append("Notes must be 10,000 characters or fewer.")
        if status not in STUDENT_STATUSES:
            errors.append("Choose a valid student status.")
        if dob_error:
            errors.append(dob_error)
        for error in errors:
            flash(error, "error")
        if errors:
            return render_template("student_form.html", page_title="Edit student", form_data=form_data, editing=True, student=student), 400

        before = {
            "status": student.status,
            "name_changed": False,
            "email_changed": False,
            "phone_changed": False,
            "date_of_birth_changed": False,
        }
        after = {
            "status": status,
            "name_changed": full_name != student.full_name,
            "email_changed": email != student.email,
            "phone_changed": phone != student.phone,
            "date_of_birth_changed": dob != student.date_of_birth,
        }
        student.full_name = full_name
        student.email = email
        student.phone = phone
        student.date_of_birth = dob
        student.status = status
        student.notes = notes
        _audit("student_updated", "student", student.id, before=before, after=after)
        db.session.commit()
        flash("Student profile updated. Existing enrollments and identifiers were preserved.", "success")
        return redirect(url_for("students.detail", student_id=student.id))
    return render_template("student_form.html", page_title="Edit student", form_data=form_data, editing=True, student=student)


@bp.post("/students/<int:student_id>/identifiers/prn")
@roles_required("admin")
def add_prn(student_id):
    student = db.get_or_404(Student, student_id)
    value = (request.form.get("prn") or "").strip()
    normalized = "".join(value.upper().split())
    if not value or len(value) > 100 or len(normalized) < 3:
        flash("Enter a PRN between 3 and 100 characters.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    if _active_prns(student):
        flash("This student already has a pending or verified PRN. A replacement requires a reviewed identity-correction workflow.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    duplicate = db.session.scalar(select(StudentIdentifier.id).where(
        StudentIdentifier.identifier_type == "prn",
        StudentIdentifier.normalized_value == normalized,
    ))
    if duplicate:
        flash("This PRN is already on another or historical student record. No link was made.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    identifier = StudentIdentifier(
        student_id=student.id,
        identifier_type="prn",
        identifier_value=value,
        verification_status="pending",
        source_note="Manually entered by an administrator; awaiting verification.",
    )
    db.session.add(identifier)
    try:
        db.session.flush()
        _audit(
            "student_prn_submitted", "student_identifier", identifier.id,
            after={"student_id": student.id, "identifier_type": "prn", "verification_status": "pending"},
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This PRN conflicts with an existing identifier. No change was saved.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    flash("PRN saved as pending. An authorized administrator must verify it before it is treated as a verified identifier.", "success")
    return redirect(url_for("students.detail", student_id=student.id))


@bp.post("/students/<int:student_id>/identifiers/<int:identifier_id>/verify")
@roles_required("admin")
def verify_prn(student_id, identifier_id):
    student = db.get_or_404(Student, student_id)
    identifier = db.get_or_404(StudentIdentifier, identifier_id)
    if identifier.student_id != student.id or identifier.identifier_type != "prn":
        abort(404)
    if identifier.verification_status != "pending":
        flash("Only a pending PRN can be verified.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    competing_verified = db.session.scalar(select(StudentIdentifier.id).where(
        StudentIdentifier.identifier_type == "prn",
        StudentIdentifier.normalized_value == identifier.normalized_value,
        StudentIdentifier.verification_status == "verified",
        StudentIdentifier.id != identifier.id,
    ))
    if competing_verified:
        flash("A verified record already uses this PRN. This record remains pending for review.", "error")
        return redirect(url_for("students.detail", student_id=student.id))
    identifier.verification_status = "verified"
    identifier.verified_by_id = current_user.id
    identifier.verified_at = utcnow()
    _audit(
        "student_prn_verified", "student_identifier", identifier.id,
        before={"verification_status": "pending"},
        after={"student_id": student.id, "identifier_type": "prn", "verification_status": "verified"},
    )
    db.session.commit()
    flash("PRN verified and linked to the existing student profile.", "success")
    return redirect(url_for("students.detail", student_id=student.id))


@bp.post("/students/<int:student_id>/enrollments")
@roles_required("admin")
def add_enrollment(student_id):
    student = db.get_or_404(Student, student_id)
    data = request.form
    form_data = data
    errors = []

    program_id, program_error = _parse_positive_id(data.get("program_id"), "Academic programme", required=True)
    if program_error:
        errors.append(program_error)
    program = db.session.get(AcademicProgram, program_id) if program_id is not None else None
    if program_id is not None and (not program or not program.is_active):
        errors.append("Choose an active academic program.")

    academic_year, year_error = _validate_academic_year(data.get("academic_year"))
    if year_error:
        errors.append(year_error)
    try:
        semester_number = int((data.get("semester_number") or "").strip())
    except ValueError:
        semester_number = 0
    if not 1 <= semester_number <= 12:
        errors.append("Semester must be between 1 and 12.")

    enrollment_type = (data.get("enrollment_type") or "regular").strip()
    if enrollment_type not in ENROLLMENT_TYPES:
        errors.append("Choose a valid enrollment type.")
    enrollment_status = (data.get("enrollment_status") or "enrolled").strip()
    if enrollment_status not in ENROLLMENT_STATUSES:
        errors.append("Choose a valid enrollment status.")

    admission_year, admission_error = _parse_year(data.get("admission_year"), "Actual admission year", required=True)
    if admission_error:
        errors.append(admission_error)
    curriculum_cohort_year, cohort_error = _parse_year(data.get("curriculum_cohort_year"), "Curriculum cohort year")
    if cohort_error:
        errors.append(cohort_error)

    raw_version_id = (data.get("curriculum_version_id") or "").strip()
    curriculum_version = None
    if raw_version_id:
        try:
            version_id = int(raw_version_id)
        except ValueError:
            version_id = 0
            errors.append("Choose a valid curriculum version.")
        curriculum_version = db.session.get(CurriculumVersion, version_id) if version_id else None
        if not curriculum_version or curriculum_version.status not in CURRICULUM_USABLE_STATUSES:
            errors.append("Choose an approved or active curriculum version.")
        elif program and curriculum_version.program_id != program.id:
            errors.append("The selected curriculum version belongs to a different programme.")
        elif curriculum_version:
            if curriculum_cohort_year is not None and curriculum_cohort_year != curriculum_version.effective_cohort_year:
                errors.append("Curriculum cohort year must match the selected curriculum version's effective cohort year.")
            curriculum_cohort_year = curriculum_version.effective_cohort_year
    elif curriculum_cohort_year is None:
        if enrollment_type == "DSE":
            errors.append("For a DSE student, specify the curriculum cohort year separately from the actual admission year.")
        else:
            curriculum_cohort_year = admission_year

    if admission_year is not None and academic_year:
        start_year = int(academic_year[:4])
        if admission_year > start_year:
            errors.append("Actual admission year cannot be later than the enrollment academic year.")

    existing_enrollments = db.session.execute(
        select(StudentEnrollment).where(StudentEnrollment.student_id == student.id)
    ).scalars().all()
    if not existing_enrollments:
        if enrollment_type == "DSE" and semester_number != 3:
            errors.append("A new DSE student's first college enrollment must start at Semester 3. Record verified prior history separately if it exists.")
        elif enrollment_type == "regular" and semester_number != 1:
            errors.append("A new regular first-year student's initial enrollment must be Semester 1. Use transfer or re-admit only when applicable and documented.")
    existing_admission_years = {row.admission_year for row in existing_enrollments if row.admission_year is not None}
    if admission_year is not None and existing_admission_years and admission_year not in existing_admission_years:
        errors.append("Actual admission year is preserved across enrollments and cannot be changed here.")

    roll_number = (data.get("roll_number") or "").strip() or None
    division = (data.get("division") or "").strip().upper() or None
    practical_batch = (data.get("practical_batch") or "").strip().upper() or None
    notes = (data.get("notes") or "").strip() or None
    if roll_number and len(roll_number) > 32:
        errors.append("Roll number must be 32 characters or fewer.")
    if division and len(division) > 16:
        errors.append("Division must be 16 characters or fewer.")
    if practical_batch and len(practical_batch) > 24:
        errors.append("Practical batch must be 24 characters or fewer.")
    if notes and len(notes) > 10000:
        errors.append("Enrollment notes must be 10,000 characters or fewer.")

    enrolled_at, enrolled_at_error = _parse_optional_date(data.get("enrolled_at"), "Enrollment date")
    if enrolled_at_error:
        errors.append(enrolled_at_error)

    if roll_number and program and academic_year and 1 <= semester_number <= 12:
        roll_scope = [
            StudentEnrollment.program_id == program.id,
            StudentEnrollment.academic_year == academic_year,
            StudentEnrollment.semester_number == semester_number,
            func.lower(StudentEnrollment.roll_number) == roll_number.lower(),
            StudentEnrollment.roll_number.is_not(None),
            StudentEnrollment.student_id != student.id,
        ]
        if division:
            roll_scope.append(func.lower(StudentEnrollment.division) == division.lower())
        else:
            roll_scope.append(StudentEnrollment.division.is_(None))
        duplicate_roll = db.session.scalar(select(StudentEnrollment.id).where(*roll_scope))
        if duplicate_roll:
            errors.append("That roll number is already used in this programme, semester and division.")

    existing_term = None
    if academic_year and 1 <= semester_number <= 12:
        existing_term = db.session.scalar(select(StudentEnrollment.id).where(
            StudentEnrollment.student_id == student.id,
            StudentEnrollment.academic_year == academic_year,
            StudentEnrollment.semester_number == semester_number,
        ))
    if existing_term:
        errors.append("This student already has an enrollment for that academic year and semester. Existing records are not overwritten.")

    if errors:
        for error in errors:
            flash(error, "error")
        return _render_student_detail(student, enrollment_form_data=form_data, status=400)

    enrollment = StudentEnrollment(
        student_id=student.id,
        program_id=program.id,
        curriculum_version_id=curriculum_version.id if curriculum_version else None,
        academic_year=academic_year,
        semester_number=semester_number,
        roll_number=roll_number,
        division=division,
        practical_batch=practical_batch,
        enrollment_type=enrollment_type,
        enrollment_status=enrollment_status,
        admission_year=admission_year,
        curriculum_cohort_year=curriculum_cohort_year,
        enrolled_at=enrolled_at,
        notes=notes,
    )
    db.session.add(enrollment)
    try:
        db.session.flush()
        _audit(
            "student_enrollment_created", "student_enrollment", enrollment.id,
            after={
                "student_id": student.id,
                "program_id": program.id,
                "academic_year": academic_year,
                "semester_number": semester_number,
                "roll_number": roll_number,
                "division": division,
                "practical_batch": practical_batch,
                "enrollment_type": enrollment_type,
                "enrollment_status": enrollment_status,
                "admission_year": admission_year,
                "curriculum_cohort_year": curriculum_cohort_year,
                "curriculum_version_id": curriculum_version.id if curriculum_version else None,
            },
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Enrollment conflicts with an existing record. No existing history was changed.", "error")
        return _render_student_detail(db.get_or_404(Student, student_id), enrollment_form_data=form_data, status=409)

    if not curriculum_version:
        flash("Enrollment saved. No curriculum version is assigned yet; subject-level academic operations should wait for the matching curriculum to be approved.", "warning")
    else:
        flash("Semester enrollment created. Earlier enrollment history remains unchanged.", "success")
    return redirect(url_for("students.detail", student_id=student.id, _anchor="enrollment-history"))
