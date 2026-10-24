"""Internal assessment, legacy calculation previews, and coursework workflow."""
from datetime import date
from decimal import Decimal, InvalidOperation
import csv
import io
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    AcademicPolicy, AcademicProgram, Assessment, AssessmentMark, AuditEvent,
    CurriculumSubject, CurriculumVersion, Student, StudentEnrollment, StudentIdentifier,
)
from ..services.legacy_mark_service import (
    LEGACY_PROFILE_ID, PolicyPendingError, calculate_lab, calculate_theory,
)
from ..services.security import roles_required
from ..services.spreadsheet_imports import (
    SpreadsheetImportError, delete_import_preview, parse_spreadsheet, read_import_preview, store_import_preview,
)

bp = Blueprint("coursework", __name__)
ATTEMPT_TYPES = {"ct1", "ct2", "assignment", "midsem", "external", "lab_ca1", "lab_ca2", "other"}
MARK_STATUSES = {"present", "absent", "exempt", "withheld"}
ENROLLMENT_ACTIVE = ("enrolled", "detained")


def _safe_csv_value(value):
    """Neutralize spreadsheet formula-leading text in CSV downloads."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _audit(action, entity_type, entity_id, before=None, after=None, reason=None):
    db.session.add(AuditEvent(
        actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id),
        before_json=before, after_json=after, reason=reason,
    ))


def _year(value):
    value = (value or "").strip()
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        return None
    start, end = int(value[:4]), int(value[5:])
    return value if 1990 <= start <= 2199 and end == (start + 1) % 100 else None


def _subject_options():
    return db.session.execute(
        select(CurriculumSubject).join(CurriculumVersion)
        .where(CurriculumVersion.status.in_(("approved", "active")), CurriculumSubject.review_status == "verified")
        .order_by(CurriculumVersion.program_id, CurriculumSubject.semester_number, CurriculumSubject.subject_code)
    ).scalars().all()


def _roster(subject, academic_year, semester, division=None, practical_batch=None):
    version = subject.curriculum_version
    stmt = select(StudentEnrollment).options(selectinload(StudentEnrollment.student), selectinload(StudentEnrollment.program)).where(
        StudentEnrollment.academic_year == academic_year,
        StudentEnrollment.semester_number == semester,
        StudentEnrollment.enrollment_status.in_(ENROLLMENT_ACTIVE),
        StudentEnrollment.program_id == version.program_id,
        or_(StudentEnrollment.curriculum_version_id == version.id, StudentEnrollment.curriculum_version_id.is_(None)),
    )
    if division:
        stmt = stmt.where(StudentEnrollment.division == division)
    if practical_batch:
        stmt = stmt.where(StudentEnrollment.practical_batch == practical_batch)
    return db.session.execute(stmt.order_by(StudentEnrollment.division, StudentEnrollment.roll_number, StudentEnrollment.student_id)).scalars().all()


def _parse_decimal(raw, label, errors, required=False):
    raw = (raw or "").strip()
    if not raw:
        if required:
            errors.append(f"{label} is required.")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{label} must be numeric.")
        return None
    if not value.is_finite():
        errors.append(f"{label} must be a finite number.")
        return None
    return value


def _resolve_import_enrollment(values, roster, verified_prns):
    """Resolve by stable code, term-scoped roll or verified PRN; never by name."""
    candidate_sets = []
    labels = []
    student_code = (values.get("student_code") or "").strip().upper()
    roll_number = (values.get("roll_number") or "").strip().upper()
    prn = "".join((values.get("prn") or "").upper().split())
    if student_code:
        matches = {e.id for e in roster if e.student.student_code.upper() == student_code}
        if not matches:
            return None, "Student code did not match an enrollment in this assessment's programme/year/semester."
        candidate_sets.append(matches); labels.append("student_code")
    if roll_number:
        matches = {e.id for e in roster if (e.roll_number or "").strip().upper() == roll_number}
        if not matches:
            return None, "Roll number did not match an enrollment in this assessment's programme/year/semester."
        candidate_sets.append(matches); labels.append("roll_number")
    if prn:
        student_ids = verified_prns.get(prn, set())
        matches = {e.id for e in roster if e.student_id in student_ids}
        if not matches:
            return None, "PRN did not match a verified PRN for an enrollment in this assessment's programme/year/semester."
        candidate_sets.append(matches); labels.append("verified_prn")
    if not candidate_sets:
        return None, "At least one identity value (student_code, roll_number or verified PRN) is required."
    intersection = set.intersection(*candidate_sets)
    if len(intersection) != 1:
        if intersection:
            return None, "Identity columns are ambiguous; provide a stable student_code or verified PRN."
        return None, "The supplied identity columns point to different students."
    return next(iter(intersection)), None


def _mark_import_preview(assessment, parsed_records):
    roster = _roster(assessment.subject, assessment.academic_year, assessment.semester_number)
    student_ids = [e.student_id for e in roster]
    verified_prns = {}
    if student_ids:
        identifiers = db.session.execute(select(StudentIdentifier).where(
            StudentIdentifier.student_id.in_(student_ids), StudentIdentifier.identifier_type == "prn",
            StudentIdentifier.verification_status == "verified")).scalars().all()
        for identifier in identifiers:
            verified_prns.setdefault(identifier.normalized_value, set()).add(identifier.student_id)
    existing = {m.enrollment_id: m for m in db.session.execute(select(AssessmentMark).where(
        AssessmentMark.assessment_id == assessment.id)).scalars().all()}
    seen = set()
    output = []
    for record in parsed_records:
        values = record.get("values", {})
        errors = []
        if record.get("parse_error"):
            errors.append(record["parse_error"])
        enrollment_id, match_error = _resolve_import_enrollment(values, roster, verified_prns)
        if match_error:
            errors.append(match_error)
        if enrollment_id and enrollment_id in seen:
            errors.append("This enrollment appears more than once in the spreadsheet.")
        if enrollment_id:
            seen.add(enrollment_id)
        status = (values.get("status") or "").strip().lower()
        status_aliases = {"p": "present", "a": "absent", "w": "withheld", "e": "exempt"}
        status = status_aliases.get(status, status)
        if status not in MARK_STATUSES:
            errors.append("Status must be present, absent, exempt or withheld.")
        marks_value = None
        raw_marks = (values.get("marks") or "").strip()
        if status == "present":
            marks_value = _parse_decimal(raw_marks, f"Row {record['row_number']} marks", errors, required=True)
            if marks_value is not None and not Decimal("0") <= marks_value <= Decimal(assessment.max_marks):
                errors.append(f"Marks must be from 0 to {assessment.max_marks}.")
        elif raw_marks:
            errors.append("Marks must be blank when status is absent, exempt or withheld.")
        reason = (values.get("correction_reason") or "").strip()
        old = existing.get(enrollment_id) if enrollment_id else None
        if old:
            current_marks = Decimal(old.marks_obtained) if old.marks_obtained is not None else None
            if old.status != status or current_marks != marks_value:
                if len(reason) < 8:
                    errors.append("Changing existing marks requires a correction_reason of at least 8 characters.")
        enrollment = next((e for e in roster if e.id == enrollment_id), None) if enrollment_id else None
        output.append({"row_number": record["row_number"], "student_code": enrollment.student.student_code if enrollment else values.get("student_code", ""),
            "student_name": enrollment.student.full_name if enrollment else "Unmatched", "roll_number": enrollment.roll_number if enrollment else values.get("roll_number", ""),
            "enrollment_id": enrollment_id, "status": status, "marks": str(marks_value) if marks_value is not None else None,
            "correction_reason": reason, "errors": errors, "valid": not errors})
    return output


def _apply_mark_import(assessment, rows):
    roster = {e.id: e for e in _roster(assessment.subject, assessment.academic_year, assessment.semester_number)}
    existing = {m.enrollment_id: m for m in db.session.execute(select(AssessmentMark).where(
        AssessmentMark.assessment_id == assessment.id)).scalars().all()}
    changes = 0
    for row in rows:
        enrollment_id = int(row["enrollment_id"])
        if enrollment_id not in roster:
            raise ValueError(f"Row {row['row_number']}: enrollment is no longer in this assessment roster.")
        status = row["status"]
        marks = Decimal(row["marks"]) if row.get("marks") is not None else None
        if status not in MARK_STATUSES or (status == "present" and (marks is None or marks < 0 or marks > Decimal(assessment.max_marks))) or (status != "present" and marks is not None):
            raise ValueError(f"Row {row['row_number']}: status/marks changed or failed validation; upload a fresh preview.")
        old = existing.get(enrollment_id)
        before = {"marks_obtained": str(old.marks_obtained) if old and old.marks_obtained is not None else None, "status": old.status if old else None}
        if old:
            old_marks = Decimal(old.marks_obtained) if old.marks_obtained is not None else None
            if old.status == status and old_marks == marks:
                continue
            if len((row.get("correction_reason") or "").strip()) < 8:
                raise ValueError(f"Row {row['row_number']}: correction reason is required for a changed record.")
            old.marks_obtained = marks; old.status = status; old.entered_by_id = current_user.id
            old.correction_reason = row.get("correction_reason")
            _audit("assessment_mark_corrected_by_import", "assessment_mark", old.id, before=before,
                   after={"marks_obtained": str(marks) if marks is not None else None, "status": status}, reason=row.get("correction_reason"))
        else:
            new_mark = AssessmentMark(assessment_id=assessment.id, enrollment_id=enrollment_id, marks_obtained=marks,
                                      status=status, entered_by_id=current_user.id, correction_reason=None)
            db.session.add(new_mark); db.session.flush()
            _audit("assessment_mark_imported", "assessment_mark", new_mark.id,
                   after={"assessment_id": assessment.id, "enrollment_id": enrollment_id,
                          "marks_obtained": str(marks) if marks is not None else None, "status": status})
        changes += 1
    return changes


@bp.get("/coursework")
@login_required
def index():
    assessments = db.session.execute(
        select(Assessment).options(selectinload(Assessment.subject).selectinload(CurriculumSubject.curriculum_version))
        .order_by(Assessment.academic_year.desc(), Assessment.semester_number.desc(), Assessment.created_at.desc()).limit(100)
    ).scalars().all()
    subjects = _subject_options()
    return render_template("coursework.html", page_title="Coursework & attendance", assessments=assessments, subjects=subjects,
                           current_year=current_app.config["CURRENT_ACADEMIC_YEAR"], assessment_types=sorted(ATTEMPT_TYPES))


@bp.post("/coursework/assessments/create")
@roles_required("admin", "faculty")
def create_assessment():
    errors = []
    try:
        subject_id = int(request.form.get("subject_id", ""))
        semester = int(request.form.get("semester_number", ""))
    except ValueError:
        subject_id, semester = 0, 0
    subject = db.session.get(CurriculumSubject, subject_id) if subject_id else None
    academic_year = _year(request.form.get("academic_year"))
    assessment_type = (request.form.get("assessment_type") or "").strip().lower()
    title = " ".join((request.form.get("title") or "").split())
    max_marks = _parse_decimal(request.form.get("max_marks"), "Maximum marks", errors, required=True)
    if not subject or subject.review_status != "verified" or subject.curriculum_version.status not in ("approved", "active"):
        errors.append("Choose a verified subject from an approved/active curriculum.")
    if not academic_year:
        errors.append("Academic year must use the format YYYY-YY.")
    if not subject or semester != subject.semester_number:
        errors.append("Semester must match the selected curriculum subject.")
    if assessment_type not in ATTEMPT_TYPES:
        errors.append("Choose a valid assessment type.")
    if not title or len(title) > 160:
        errors.append("Assessment title is required and must be at most 160 characters.")
    if max_marks is not None and (max_marks <= 0 or max_marks > Decimal("10000")):
        errors.append("Maximum marks must be greater than 0 and no more than 10000.")
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("coursework.index"))
    assessment = Assessment(
        curriculum_subject_id=subject.id, academic_year=academic_year, semester_number=semester,
        assessment_type=assessment_type, title=title, max_marks=max_marks,
        held_on=date.fromisoformat(request.form["held_on"]) if request.form.get("held_on") else None,
        created_by_id=current_user.id, status="draft",
    )
    db.session.add(assessment)
    try:
        db.session.flush()
        _audit("assessment_created", "assessment", assessment.id, after={"subject_id": subject.id, "year": academic_year,
               "semester": semester, "type": assessment_type, "max_marks": str(max_marks)})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The assessment could not be saved because it conflicts with an existing record.", "error")
        return redirect(url_for("coursework.index"))
    flash("Assessment created. Enter marks in the roster, then publish or lock it when ready.", "success")
    return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))


@bp.route("/coursework/assessments/<int:assessment_id>", methods=["GET", "POST"])
@roles_required("admin", "faculty")
def assessment_detail(assessment_id):
    assessment = db.get_or_404(Assessment, assessment_id)
    if assessment.status == "locked" and request.method == "POST":
        flash("This assessment is locked. Unlocking requires an administrator review.", "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    roster = _roster(assessment.subject, assessment.academic_year, assessment.semester_number)
    existing = {m.enrollment_id: m for m in db.session.execute(select(AssessmentMark).where(AssessmentMark.assessment_id == assessment.id)).scalars()}
    if request.method == "POST":
        errors = []
        changes = 0
        for enrollment in roster:
            raw_status = (request.form.get(f"status_{enrollment.id}") or "").strip().lower()
            raw_marks = (request.form.get(f"marks_{enrollment.id}") or "").strip()
            old = existing.get(enrollment.id)
            if not raw_status and not raw_marks and not old:
                # Blank rows are intentionally left unrecorded so faculty can enter marks in batches.
                continue
            if raw_status not in MARK_STATUSES:
                errors.append(f"Choose a status for {enrollment.student.full_name}.")
                continue
            value = None
            if raw_status == "present":
                value = _parse_decimal(raw_marks, f"Marks for {enrollment.student.full_name}", errors, required=True)
                if value is not None and (value < 0 or value > Decimal(assessment.max_marks)):
                    errors.append(f"Marks for {enrollment.student.full_name} must be between 0 and {assessment.max_marks}.")
                    continue
            before = {"marks_obtained": str(old.marks_obtained) if old and old.marks_obtained is not None else None,
                      "status": old.status if old else None}
            if old:
                old_marks = Decimal(old.marks_obtained) if old.marks_obtained is not None else None
                if before["status"] == raw_status and old_marks == value:
                    continue
                correction_reason = (request.form.get(f"reason_{enrollment.id}") or "").strip()
                if len(correction_reason) < 8:
                    errors.append(f"A correction reason of at least 8 characters is required for {enrollment.student.full_name}.")
                    continue
                old.marks_obtained = value
                old.status = raw_status
                old.entered_by_id = current_user.id
                old.correction_reason = correction_reason
                _audit("assessment_mark_corrected", "assessment_mark", old.id, before=before,
                       after={"marks_obtained": str(value) if value is not None else None, "status": raw_status}, reason=old.correction_reason)
            else:
                mark = AssessmentMark(assessment_id=assessment.id, enrollment_id=enrollment.id, marks_obtained=value,
                                      status=raw_status, entered_by_id=current_user.id)
                db.session.add(mark)
                db.session.flush()
                _audit("assessment_mark_entered", "assessment_mark", mark.id, after={"assessment_id": assessment.id,
                       "enrollment_id": enrollment.id, "marks_obtained": str(value) if value is not None else None, "status": raw_status})
            changes += 1
        if errors:
            db.session.rollback()
            for message in errors[:12]:
                flash(message, "error")
            flash("No marks were saved because at least one row needs correction.", "error")
            return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Marks conflicted with another update. Reload the roster and retry.", "error")
            return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
        flash(f"Saved {changes} mark change(s). The audit trail records prior values for corrections.", "success")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    roster_rows = [{"enrollment": enrollment, "mark": existing.get(enrollment.id)} for enrollment in roster]
    return render_template("assessment_detail.html", page_title=assessment.title, assessment=assessment, roster_rows=roster_rows)


@bp.post("/coursework/assessments/<int:assessment_id>/status")
@roles_required("admin", "faculty")
def assessment_status(assessment_id):
    assessment = db.get_or_404(Assessment, assessment_id)
    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in {"draft", "published", "locked", "archived"}:
        abort(400)
    if assessment.status == "locked" and current_user.role != "admin":
        abort(403)
    old = assessment.status
    assessment.status = new_status
    _audit("assessment_status_changed", "assessment", assessment.id, before={"status": old}, after={"status": new_status})
    db.session.commit()
    flash(f"Assessment status changed to {new_status.replace('_', ' ')}.", "success")
    return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))


def _mark_lookup(assessment_list, enrollment_id):
    lookup = {}
    for assessment in assessment_list:
        mark = next((m for m in assessment.marks if m.enrollment_id == enrollment_id), None)
        if mark:
            lookup[assessment.assessment_type.lower()] = (assessment, mark)
    return lookup


@bp.get("/coursework/subjects/<int:subject_id>/summary")
@login_required
def subject_summary(subject_id):
    subject = db.get_or_404(CurriculumSubject, subject_id)
    year = _year(request.args.get("academic_year")) or current_app.config["CURRENT_ACADEMIC_YEAR"]
    semester = request.args.get("semester", type=int) or subject.semester_number
    if semester != subject.semester_number:
        abort(400)
    assessments = db.session.execute(
        select(Assessment).options(selectinload(Assessment.marks))
        .where(Assessment.curriculum_subject_id == subject.id, Assessment.academic_year == year,
               Assessment.semester_number == semester, Assessment.status != "archived")
        .order_by(Assessment.held_on, Assessment.created_at)
    ).scalars().all()
    roster = _roster(subject, year, semester)
    rows = []
    is_lab = subject.course_type.lower() in {"lab", "project", "practical"}
    expected = {"ct1": Decimal(10), "ct2": Decimal(10), "assignment": Decimal(10), "midsem": Decimal(20), "external": Decimal(40 if is_lab else 60), "lab_ca1": Decimal(30), "lab_ca2": Decimal(30)}
    active_policy = db.session.scalar(select(AcademicPolicy).where(
        AcademicPolicy.policy_type == "grading", AcademicPolicy.status == "active",
        AcademicPolicy.rules_json["calculation_profile"].as_string() == LEGACY_PROFILE_ID,
        or_(AcademicPolicy.curriculum_version_id == subject.curriculum_version_id, AcademicPolicy.curriculum_version_id.is_(None)),
        or_(AcademicPolicy.program_id == subject.curriculum_version.program_id, AcademicPolicy.program_id.is_(None)),
    ).order_by(AcademicPolicy.curriculum_version_id.desc(), AcademicPolicy.program_id.desc()))
    for enrollment in roster:
        lookup = _mark_lookup(assessments, enrollment.id)
        vals = {}
        max_mismatch = []
        for key in expected:
            if key not in lookup:
                vals[key] = None
                continue
            assessment, mark = lookup[key]
            if Decimal(assessment.max_marks) != expected[key]:
                max_mismatch.append(f"{assessment.title} is /{assessment.max_marks}, legacy profile expects /{expected[key]} for {key}.")
            if mark.status == "absent":
                vals[key] = 0
            elif mark.status == "present" and mark.marks_obtained is not None:
                vals[key] = float(mark.marks_obtained)
            else:
                vals[key] = None
        result = None
        warning = None
        try:
            if max_mismatch:
                warning = "Assessment maximums do not match the legacy formula: " + " ".join(max_mismatch)
            elif is_lab:
                result = calculate_lab(ca1=vals.get("lab_ca1"), ca2=vals.get("lab_ca2"), external=vals.get("external"),
                                       credits=subject.credits or 0, policy=active_policy, authoritative=bool(active_policy))
            else:
                result = calculate_theory(ct1=vals.get("ct1"), ct2=vals.get("ct2"), assignment=vals.get("assignment"),
                                          midsem=vals.get("midsem"), external=vals.get("external"),
                                          credits=subject.credits or 0, policy=active_policy, authoritative=bool(active_policy))
        except (ValueError, PolicyPendingError) as exc:
            warning = str(exc)
        if result and not active_policy:
            warning = "Preview only: no active grading policy maps to the legacy calculation profile. Do not treat as an official result."
        rows.append({"enrollment": enrollment, "values": vals, "result": result, "warning": warning,
                     "policy_status": "active profile" if active_policy else "policy pending"})
    if request.method == "POST":
        # Intentional no write from GET; calculations are only previews until snapshot workflow is added.
        abort(405)
    return render_template("subject_summary.html", page_title=f"Calculation preview · {subject.subject_code}", subject=subject,
                           rows=rows, academic_year=year, semester=semester, is_lab=is_lab)


@bp.post("/coursework/assessments/<int:assessment_id>/import-preview")
@roles_required("admin", "faculty")
def import_preview(assessment_id):
    assessment = db.get_or_404(Assessment, assessment_id)
    if assessment.status == "locked":
        flash("This assessment is locked and cannot accept an import.", "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose a CSV or XLSX marks file.", "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    raw = upload.stream.read(16 * 1024 * 1024 + 1)
    try:
        parsed = parse_spreadsheet(raw, upload.filename, workflow="coursework")
        rows = _mark_import_preview(assessment, parsed)
        token, payload = store_import_preview(upload_root=current_app.config["UPLOAD_ROOT"], workflow="coursework",
            target_id=assessment.id, actor_id=current_user.id, filename=upload.filename, content=raw, rows=rows)
    except SpreadsheetImportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    payload["can_commit"] = bool(rows) and all(row["valid"] for row in rows)
    payload["valid_count"] = sum(1 for row in rows if row["valid"])
    payload["error_count"] = sum(len(row["errors"]) for row in rows)
    (Path(current_app.config["UPLOAD_ROOT"]) / "import_previews" / f"{token}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return render_template("spreadsheet_import_preview.html", page_title="Review marks import", workflow="coursework",
        target=assessment, rows=rows, payload=payload, token=token, back_url=url_for("coursework.assessment_detail", assessment_id=assessment.id),
        confirm_url=url_for("coursework.confirm_import", assessment_id=assessment.id, token=token))


@bp.post("/coursework/assessments/<int:assessment_id>/import-confirm/<token>")
@roles_required("admin", "faculty")
def confirm_import(assessment_id, token):
    assessment = db.get_or_404(Assessment, assessment_id)
    try:
        preview_path, payload = read_import_preview(upload_root=current_app.config["UPLOAD_ROOT"], token=token,
            workflow="coursework", target_id=assessment.id, actor_id=current_user.id)
    except SpreadsheetImportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    if assessment.status == "locked" or any(row.get("errors") for row in payload.get("rows", [])) or not payload.get("rows"):
        flash("This preview is invalid or the assessment is locked. Correct the spreadsheet and preview it again.", "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    try:
        changes = _apply_mark_import(assessment, payload["rows"])
        db.session.add(AuditEvent(actor_id=current_user.id, action="coursework_spreadsheet_import_committed",
            entity_type="assessment", entity_id=str(assessment.id),
            after_json={"source_filename": payload["source_filename"], "source_storage_key": payload["source_storage_key"],
                        "sha256": payload["source_sha256"], "data_rows": len(payload["rows"]), "changes": changes}))
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Import was not committed; no partial changes were saved. {exc}", "error")
        return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))
    delete_import_preview(preview_path)
    flash(f"Spreadsheet import committed atomically: {changes} mark record(s) created or corrected. Original file and source hash were retained.", "success")
    return redirect(url_for("coursework.assessment_detail", assessment_id=assessment.id))


@bp.get("/coursework/export.csv")
@login_required
def export_marks_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["student_code", "student_name", "academic_year", "semester", "roll_number", "division", "subject_code", "assessment_type", "assessment_title", "marks_obtained", "max_marks", "status"])
    query = db.session.execute(
        select(AssessmentMark, Assessment, StudentEnrollment, Student, CurriculumSubject)
        .join(Assessment, Assessment.id == AssessmentMark.assessment_id)
        .join(StudentEnrollment, StudentEnrollment.id == AssessmentMark.enrollment_id)
        .join(Student, Student.id == StudentEnrollment.student_id)
        .join(CurriculumSubject, CurriculumSubject.id == Assessment.curriculum_subject_id)
        .order_by(Assessment.academic_year, Assessment.semester_number, Student.full_name)
    ).all()
    for mark, assessment, enrollment, student, subject in query:
        writer.writerow([_safe_csv_value(value) for value in [student.student_code, student.full_name, enrollment.academic_year, enrollment.semester_number,
                         enrollment.roll_number or "", enrollment.division or "", subject.subject_code, assessment.assessment_type,
                         assessment.title, str(mark.marks_obtained) if mark.marks_obtained is not None else "", assessment.max_marks, mark.status]])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="acadpulse-coursework-marks.csv"'
    return response
