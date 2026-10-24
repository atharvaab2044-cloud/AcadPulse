"""University PDF import, independent staging, reviewed linking, and attempt history."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import (
    AcademicPolicy, AcademicProgram, AuditEvent, CurriculumSubject, CurriculumVersion, ExamAttempt,
    ResultImport, StagedResult, Student, StudentEnrollment, StudentIdentifier,
)
from ..services.security import roles_required
from ..services.university_result_parser import ResultPdfError, extract_candidate_rows, normalize_identifier

bp = Blueprint("results", __name__, url_prefix="/university-results")
ATTEMPT_TYPES = {"regular", "supplementary", "revaluation", "revised", "other"}
RESULT_STATUSES = {"pass", "fail", "absent", "withheld", "provisional", "unknown"}


def _audit(action, entity_type, entity_id, before=None, after=None, reason=None):
    db.session.add(AuditEvent(actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id), before_json=before, after_json=after, reason=reason))


def _parse_optional_decimal(raw, label, errors):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{label} must be numeric.")
        return None
    if not value.is_finite() or value < 0:
        errors.append(f"{label} must be a non-negative finite number.")
        return None
    return value


def _safe_import_path(result_import):
    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    path = (root / result_import.storage_key).resolve()
    if root not in path.parents or not path.is_file():
        abort(404)
    return path


def _normalize_year(value):
    value = (value or "").strip()
    try:
        start, end = value.split("-", 1)
        if len(start) == 4 and len(end) == 2 and 1990 <= int(start) <= 2199 and int(end) == (int(start) + 1) % 100:
            return value
    except (ValueError, TypeError):
        pass
    return None


def _refresh_import_status(result_import):
    total = len(result_import.staged_results)
    if total and all(row.match_status == "linked" for row in result_import.staged_results):
        result_import.status = "linked"
    elif any(row.match_status == "linked" for row in result_import.staged_results):
        result_import.status = "partially_linked"
    elif total and all(row.review_status == "rejected" for row in result_import.staged_results):
        result_import.status = "rejected"
    elif total:
        result_import.status = "review_required"
    else:
        result_import.status = "review_required"


@bp.get("")
@login_required
def index():
    imports = db.session.execute(select(ResultImport).order_by(ResultImport.uploaded_at.desc()).limit(100)).scalars().all()
    programs = db.session.execute(select(AcademicProgram).where(AcademicProgram.is_active.is_(True)).order_by(AcademicProgram.name)).scalars().all()
    return render_template("result_imports.html", page_title="University results", imports=imports, programs=programs,
                           current_year=current_app.config["CURRENT_ACADEMIC_YEAR"])


@bp.post("/imports")
@roles_required("admin", "faculty")
def upload():
    upload_file = request.files.get("result_pdf")
    if not upload_file or not upload_file.filename:
        flash("Choose a university result PDF.", "error")
        return redirect(url_for("results.index"))
    filename = secure_filename(upload_file.filename)[:255]
    if not filename or not filename.lower().endswith(".pdf"):
        flash("Only PDF files can be imported.", "error")
        return redirect(url_for("results.index"))
    max_size = int(current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
    raw = upload_file.stream.read(max_size + 1)
    if len(raw) > max_size:
        flash(f"The PDF exceeds the {max_size // (1024 * 1024)} MB upload limit.", "error")
        return redirect(url_for("results.index"))
    if not raw.startswith(b"%PDF-"):
        flash("The uploaded file does not have a valid PDF header.", "error")
        return redirect(url_for("results.index"))
    digest = hashlib.sha256(raw).hexdigest()
    if db.session.scalar(select(ResultImport.id).where(ResultImport.source_sha256 == digest)):
        flash("This exact PDF has already been imported. Open the existing import instead of creating a duplicate.", "error")
        return redirect(url_for("results.index"))
    academic_year = _normalize_year(request.form.get("academic_year")) if request.form.get("academic_year") else None
    if request.form.get("academic_year") and not academic_year:
        flash("Academic year must use YYYY-YY format.", "error")
        return redirect(url_for("results.index"))
    semester = None
    if (request.form.get("semester_number") or "").strip():
        try:
            semester = int(request.form["semester_number"])
        except ValueError:
            semester = 0
        if not 1 <= semester <= 12:
            flash("Semester must be between 1 and 12.", "error")
            return redirect(url_for("results.index"))
    program = None
    if request.form.get("program_id"):
        try:
            program = db.session.get(AcademicProgram, int(request.form["program_id"]))
        except ValueError:
            program = None
        if not program or not program.is_active:
            flash("Choose a registered active programme or leave the field blank.", "error")
            return redirect(url_for("results.index"))
    try:
        extracted = extract_candidate_rows(raw)
    except ResultPdfError as exc:
        flash(str(exc), "error")
        return redirect(url_for("results.index"))

    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    relative_dir = Path("result_imports")
    absolute_dir = root / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{secrets.token_hex(16)}.pdf"
    storage_key = str(relative_dir / storage_name).replace("\\", "/")
    try:
        (root / storage_key).write_bytes(raw)
        result_import = ResultImport(
            import_code=f"RSL-{datetime.now(timezone.utc).year}-{secrets.token_hex(3).upper()}",
            source_filename=filename, storage_key=storage_key, source_sha256=digest,
            university_name=(request.form.get("university_name") or "").strip()[:160] or None,
            program_code=program.code if program else None, semester_number=semester, academic_year=academic_year,
            exam_session=(request.form.get("exam_session") or "").strip()[:80] or None,
            parser_name=extracted["parser_name"], parser_version=extracted["parser_version"],
            status="review_required", uploaded_by_id=current_user.id,
            notes=(f"{len(extracted['rows'])} candidate rows extracted; all require review. "
                   f"{len(extracted['scanned_or_image_only_pages'])} page(s) have little/no selectable text."),
        )
        db.session.add(result_import)
        db.session.flush()
        for candidate in extracted["rows"]:
            db.session.add(StagedResult(result_import_id=result_import.id, **candidate))
        db.session.add(AuditEvent(actor_id=current_user.id, action="university_result_pdf_uploaded", entity_type="result_import",
            entity_id=str(result_import.id), after_json={"source_filename": filename, "sha256": digest, "candidate_rows": len(extracted["rows"]),
            "page_count": extracted["page_count"], "selectable_characters": extracted["selectable_characters"]}))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        (root / storage_key).unlink(missing_ok=True)
        flash("The import conflicts with an existing record. No import was created.", "error")
        return redirect(url_for("results.index"))
    flash(f"PDF staged. {len(extracted['rows'])} candidate row(s) were detected; none were linked automatically.", "success")
    return redirect(url_for("results.detail", import_id=result_import.id))


@bp.get("/imports/<int:import_id>")
@login_required
def detail(import_id):
    result_import = db.get_or_404(ResultImport, import_id)
    rows = db.session.execute(select(StagedResult).where(StagedResult.result_import_id == result_import.id).order_by(StagedResult.page_number, StagedResult.id)).scalars().all()
    return render_template("result_import_detail.html", page_title=result_import.import_code, result_import=result_import, staged_rows=rows)


@bp.get("/imports/<int:import_id>/source")
@login_required
def source_pdf(import_id):
    result_import = db.get_or_404(ResultImport, import_id)
    return send_file(_safe_import_path(result_import), mimetype="application/pdf", as_attachment=False, download_name=result_import.source_filename)


@bp.post("/imports/<int:import_id>/staged/new")
@roles_required("admin", "faculty")
def add_staged(import_id):
    result_import = db.get_or_404(ResultImport, import_id)
    code = (request.form.get("subject_code") or "").strip().upper()
    title = " ".join((request.form.get("subject_title") or "").split())
    prn = (request.form.get("prn") or "").strip() or None
    errors = []
    if not code or len(code) > 40: errors.append("Subject code is required and must be at most 40 characters.")
    if not title or len(title) > 220: errors.append("Subject title is required and must be at most 220 characters.")
    if errors:
        for error in errors: flash(error, "error")
        return redirect(url_for("results.detail", import_id=import_id))
    row = StagedResult(result_import_id=result_import.id, extracted_prn=prn, normalized_prn=normalize_identifier(prn),
        extracted_subject_code=code, extracted_subject_title=title,
        raw_payload={"source": "manual_entry", "note": "Manually entered after PDF review."}, extracted_marks={},
        review_status="corrected", match_status="unmatched", reviewed_by_id=current_user.id,
        reviewed_at=datetime.now(timezone.utc), review_note="Manually entered by an authorized user.")
    db.session.add(row)
    try:
        db.session.flush()
        _audit("staged_result_manually_added", "staged_result", row.id, after={"import_id": result_import.id, "subject_code": code, "source": "manual"})
        _refresh_import_status(result_import)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The staged row could not be created. Please retry.", "error")
        return redirect(url_for("results.detail", import_id=import_id))
    flash("Manual result row added. Complete the marks and link it explicitly when verified.", "success")
    return redirect(url_for("results.edit_staged", staged_id=row.id))


@bp.route("/staged/<int:staged_id>/edit", methods=["GET", "POST"])
@roles_required("admin", "faculty")
def edit_staged(staged_id):
    row = db.get_or_404(StagedResult, staged_id)
    if row.match_status == "linked" and request.method == "POST":
        flash("This row is already linked to an exam attempt. The attempt is preserved; add a revised import/attempt for corrections.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    result_import = row.result_import
    programs = db.session.execute(select(AcademicProgram).where(AcademicProgram.is_active.is_(True)).order_by(AcademicProgram.name)).scalars().all()
    students = db.session.execute(select(Student).order_by(Student.full_name)).scalars().all()
    subject_query = select(CurriculumSubject).join(CurriculumVersion).where(CurriculumVersion.program_id == (db.session.scalar(select(AcademicProgram.id).where(AcademicProgram.code == result_import.program_code)) or -1)) if result_import.program_code else select(CurriculumSubject).join(CurriculumVersion)
    if result_import.semester_number:
        subject_query = subject_query.where(CurriculumSubject.semester_number == result_import.semester_number)
    subjects = db.session.execute(subject_query.order_by(CurriculumSubject.subject_code)).scalars().all()
    if request.method == "POST":
        errors = []
        prn = (request.form.get("prn") or "").strip() or None
        subject_code = (request.form.get("subject_code") or "").strip().upper() or None
        subject_title = " ".join((request.form.get("subject_title") or "").split()) or None
        marks = {}
        for key, label in [("internal", "Internal marks"), ("external", "External marks"), ("total", "Total marks")]:
            value = _parse_optional_decimal(request.form.get(key), label, errors)
            if value is not None and value > 10000:
                errors.append(f"{label} is unreasonably large.")
            marks[key] = str(value) if value is not None else None
        grade = (request.form.get("grade") or "").strip().upper() or None
        status = (request.form.get("result_status") or "unknown").strip().lower()
        note = (request.form.get("review_note") or "").strip() or None
        if not subject_code or len(subject_code) > 40: errors.append("Subject code is required and must be at most 40 characters.")
        if not subject_title or len(subject_title) > 220: errors.append("Subject title is required and must be at most 220 characters.")
        if grade and len(grade) > 24: errors.append("Grade must be at most 24 characters.")
        if status not in RESULT_STATUSES: errors.append("Choose a valid result status.")
        if note and len(note) > 5000: errors.append("Review note must be at most 5000 characters.")
        if errors:
            for error in errors: flash(error, "error")
            return redirect(url_for("results.edit_staged", staged_id=row.id))
        before = {"prn": row.extracted_prn, "subject_code": row.extracted_subject_code, "subject_title": row.extracted_subject_title,
                  "marks": row.extracted_marks, "grade": row.extracted_grade, "status": row.extracted_result_status}
        row.extracted_prn = prn
        row.normalized_prn = normalize_identifier(prn)
        row.extracted_subject_code = subject_code
        row.extracted_subject_title = subject_title
        row.extracted_marks = marks
        row.extracted_grade = grade
        row.extracted_result_status = status
        row.review_status = "corrected"
        row.reviewed_by_id = current_user.id
        row.reviewed_at = datetime.now(timezone.utc)
        row.review_note = note
        _audit("staged_result_corrected", "staged_result", row.id, before=before,
            after={"prn": prn, "subject_code": subject_code, "subject_title": subject_title, "marks": marks, "grade": grade, "status": status}, reason=note)
        _refresh_import_status(result_import)
        db.session.commit()
        flash("Staged row saved as corrected. It is still not linked to the official student record.", "success")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    return render_template("result_staged_edit.html", page_title="Review result row", row=row, result_import=result_import,
        students=students, subjects=subjects, programs=programs)


@bp.post("/staged/<int:staged_id>/link")
@roles_required("admin")
def link_staged(staged_id):
    row = db.get_or_404(StagedResult, staged_id)
    result_import = row.result_import
    if row.match_status == "linked":
        flash("This staged result is already linked.", "error")
        return redirect(url_for("results.detail", import_id=result_import.id))
    if row.review_status not in ("corrected", "approved"):
        flash("Review and correct this row before linking it.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    if not result_import.academic_year or not result_import.semester_number:
        flash("The import needs an academic year and semester before a result can be linked.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    try:
        student_id = int(request.form.get("student_id", ""))
        subject_id = int(request.form.get("subject_id", ""))
    except ValueError:
        flash("Choose a student and subject explicitly.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    student = db.session.get(Student, student_id)
    subject = db.session.get(CurriculumSubject, subject_id)
    attempt_type = (request.form.get("attempt_type") or "regular").strip().lower()
    override_reason = (request.form.get("link_reason") or "").strip()
    if not student or not subject or attempt_type not in ATTEMPT_TYPES:
        flash("Choose a valid student, subject and attempt type.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    if subject.semester_number != result_import.semester_number:
        flash("The selected subject semester does not match the import context.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    if result_import.program_code and subject.curriculum_version.program.code != result_import.program_code:
        flash("The subject programme does not match the import context.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    exact_prn = None
    if row.normalized_prn:
        exact_prn = db.session.scalar(select(StudentIdentifier.id).where(
            StudentIdentifier.student_id == student.id, StudentIdentifier.identifier_type == "prn",
            StudentIdentifier.normalized_value == row.normalized_prn, StudentIdentifier.verification_status == "verified"))
    conflicts = []
    if row.normalized_prn and not exact_prn:
        conflicts.append("Selected student does not have this exact verified PRN")
    if row.extracted_subject_code and row.extracted_subject_code.upper() != subject.subject_code.upper():
        conflicts.append("Selected subject code differs from the extracted/reviewed code")
    if conflicts and len(override_reason) < 25:
        flash("Link conflict: " + "; ".join(conflicts) + ". An administrator must provide a documented override reason of at least 25 characters or correct the row.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    if not row.normalized_prn and len(override_reason) < 25:
        flash("No PRN was extracted. Provide a documented manual-match reason of at least 25 characters.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    enrollment = db.session.scalar(select(StudentEnrollment).where(
        StudentEnrollment.student_id == student.id, StudentEnrollment.academic_year == result_import.academic_year,
        StudentEnrollment.semester_number == result_import.semester_number,
        StudentEnrollment.program_id == subject.curriculum_version.program_id,
        StudentEnrollment.enrollment_status.in_(("enrolled", "detained", "completed")),
    ).order_by(StudentEnrollment.id.desc()))
    if not enrollment:
        flash("The selected student has no matching enrollment for this programme, academic year and semester.", "error")
        return redirect(url_for("results.edit_staged", staged_id=row.id))
    if db.session.scalar(select(ExamAttempt.id).where(ExamAttempt.source_staged_result_id == row.id)):
        flash("An exam attempt already exists for this staged row.", "error")
        return redirect(url_for("results.detail", import_id=result_import.id))
    marks = row.extracted_marks or {}
    attempt = ExamAttempt(student_id=student.id, enrollment_id=enrollment.id, curriculum_subject_id=subject.id,
        source_staged_result_id=row.id, academic_year=result_import.academic_year, semester_number=result_import.semester_number,
        exam_session=result_import.exam_session, attempt_type=attempt_type,
        internal_marks=Decimal(marks["internal"]) if marks.get("internal") else None,
        external_marks=Decimal(marks["external"]) if marks.get("external") else None,
        total_marks=Decimal(marks["total"]) if marks.get("total") else None,
        official_grade=row.extracted_grade,
        official_status=row.extracted_result_status if row.extracted_result_status in RESULT_STATUSES else "unknown",
        effective_for_calculation=False, verification_status="verified", verified_by_id=current_user.id,
        verified_at=datetime.now(timezone.utc), source_note=override_reason or "Explicitly linked by administrator after source review.")
    db.session.add(attempt)
    row.linked_student_id = student.id
    row.linked_subject_id = subject.id
    row.match_status = "linked"
    row.review_status = "approved"
    row.reviewed_by_id = current_user.id
    row.reviewed_at = datetime.now(timezone.utc)
    _audit("university_result_linked", "exam_attempt", row.id,
        after={"student_id": student.id, "subject_id": subject.id, "enrollment_id": enrollment.id,
               "attempt_type": attempt_type, "effective_for_calculation": False},
        reason=override_reason or "Exact verified PRN and reviewed subject match.")
    _refresh_import_status(result_import)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The result may already have been linked. Reload the import and check attempt history.", "error")
        return redirect(url_for("results.detail", import_id=result_import.id))
    flash("Exam attempt linked and retained in history. It is not marked effective for SGPA/CGPA calculation until the applicable rule is configured.", "success")
    return redirect(url_for("results.detail", import_id=result_import.id))


@bp.post("/attempts/<int:attempt_id>/set-effective")
@roles_required("admin")
def set_effective_attempt(attempt_id):
    """Explicitly select the effective exam attempt under an approved policy.

    This never deletes or rewrites historical attempts. All conflicting attempts
    for the same enrollment/subject are retained but have their effective flag
    cleared before the selected attempt is marked effective.
    """
    attempt = db.get_or_404(ExamAttempt, attempt_id)
    reason = (request.form.get("reason") or "").strip()
    if attempt.verification_status != "verified":
        flash("Only a verified examination attempt can be selected for calculation.", "error")
        return redirect(url_for("students.detail", student_id=attempt.student_id))
    if not attempt.enrollment_id or not attempt.enrollment:
        flash("This attempt has no exact semester enrollment. Resolve the enrollment link before selecting it.", "error")
        return redirect(url_for("students.detail", student_id=attempt.student_id))
    if attempt.official_status not in {"pass", "fail", "absent"}:
        flash("An attempt with withheld, provisional or unknown status cannot be effective for calculation.", "error")
        return redirect(url_for("students.detail", student_id=attempt.student_id))
    if len(reason) < 25:
        flash("Record an approval reason of at least 25 characters explaining why this attempt is effective.", "error")
        return redirect(url_for("students.detail", student_id=attempt.student_id))

    enrollment = attempt.enrollment
    policies = db.session.execute(select(AcademicPolicy).where(
        AcademicPolicy.policy_type == "supplementary", AcademicPolicy.status == "active",
        (AcademicPolicy.program_id == enrollment.program_id) | AcademicPolicy.program_id.is_(None),
        (AcademicPolicy.curriculum_version_id == enrollment.curriculum_version_id) | AcademicPolicy.curriculum_version_id.is_(None),
        (AcademicPolicy.effective_from_cohort_year == enrollment.curriculum_cohort_year) | AcademicPolicy.effective_from_cohort_year.is_(None),
    )).scalars().all()
    policies.sort(key=lambda p: (p.curriculum_version_id is not None, p.program_id is not None,
                                 p.effective_from_cohort_year is not None, p.created_at), reverse=True)
    policy = next((p for p in policies if (p.rules_json or {}).get("effective_attempt_selection") == "admin_selected"), None)
    if not policy:
        flash("No active supplementary policy for this programme/cohort permits explicit admin-selected effective attempts. Add and approve that rule first.", "error")
        return redirect(url_for("system.policies"))

    siblings = db.session.execute(select(ExamAttempt).where(
        ExamAttempt.enrollment_id == enrollment.id,
        ExamAttempt.curriculum_subject_id == attempt.curriculum_subject_id,
        ExamAttempt.verification_status == "verified",
    )).scalars().all()
    previous_ids = [other.id for other in siblings if other.effective_for_calculation]
    for other in siblings:
        other.effective_for_calculation = (other.id == attempt.id)
    _audit("exam_attempt_selected_effective", "exam_attempt", attempt.id,
           before={"effective_attempt_ids": previous_ids},
           after={"effective_attempt_id": attempt.id, "policy_code": policy.policy_code}, reason=reason)
    db.session.commit()
    flash("Effective attempt recorded under the active supplementary policy. Previous attempts remain in history; recalculate SGPA as a new snapshot.", "success")
    return redirect(url_for("students.detail", student_id=attempt.student_id, _anchor="university-attempts"))


@bp.post("/staged/<int:staged_id>/reject")
@roles_required("admin")
def reject_staged(staged_id):
    row = db.get_or_404(StagedResult, staged_id)
    if row.match_status == "linked":
        flash("A linked attempt cannot be removed by rejecting the import row. Use a corrected/revised attempt workflow.", "error")
        return redirect(url_for("results.detail", import_id=row.result_import_id))
    note = (request.form.get("reason") or "").strip()
    if len(note) < 8:
        flash("Provide a short reason for rejection (at least 8 characters).", "error")
        return redirect(url_for("results.detail", import_id=row.result_import_id))
    row.review_status = "rejected"
    row.review_note = note
    row.reviewed_by_id = current_user.id
    row.reviewed_at = datetime.now(timezone.utc)
    _audit("staged_result_rejected", "staged_result", row.id, after={"review_status": "rejected"}, reason=note)
    _refresh_import_status(row.result_import)
    db.session.commit()
    flash("Staged row rejected. The source PDF and review event remain preserved.", "success")
    return redirect(url_for("results.detail", import_id=row.result_import_id))
