"""Session-based attendance with class/batch-scoped rosters and an audit trail."""
from datetime import date
from decimal import Decimal
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
from ..models import AttendanceRecord, AttendanceSession, AuditEvent, CurriculumSubject, CurriculumVersion, StudentEnrollment, StudentIdentifier
from ..services.security import roles_required
from ..services.spreadsheet_imports import (
    SpreadsheetImportError, delete_import_preview, parse_spreadsheet, read_import_preview, store_import_preview,
)

bp = Blueprint("attendance", __name__)
VALID_ATTENDANCE = {"present", "absent", "excused"}


def _safe_csv_value(value):
    """Neutralize spreadsheet formula-leading text in CSV downloads."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _audit(action, entity_type, entity_id, before=None, after=None, reason=None):
    db.session.add(AuditEvent(actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id), before_json=before, after_json=after, reason=reason))


def _academic_year(value):
    value = (value or "").strip()
    try:
        start, end = value.split("-", 1)
        if len(start) == 4 and len(end) == 2 and int(start) >= 1990 and int(end) == (int(start) + 1) % 100:
            return value
    except (ValueError, TypeError):
        pass
    return None


def _subjects():
    return db.session.execute(select(CurriculumSubject).join(CurriculumVersion).where(
        CurriculumSubject.review_status == "verified", CurriculumVersion.status.in_(("approved", "active"))
    ).order_by(CurriculumVersion.program_id, CurriculumSubject.semester_number, CurriculumSubject.subject_code)).scalars().all()


def _roster(session):
    version = session.subject.curriculum_version
    stmt = select(StudentEnrollment).options(selectinload(StudentEnrollment.student)).where(
        StudentEnrollment.program_id == version.program_id,
        StudentEnrollment.academic_year == session.academic_year,
        StudentEnrollment.semester_number == session.semester_number,
        StudentEnrollment.enrollment_status.in_(("enrolled", "detained")),
        or_(StudentEnrollment.curriculum_version_id == version.id, StudentEnrollment.curriculum_version_id.is_(None)),
    )
    if session.division:
        stmt = stmt.where(StudentEnrollment.division == session.division)
    if session.practical_batch:
        stmt = stmt.where(StudentEnrollment.practical_batch == session.practical_batch)
    return db.session.execute(stmt.order_by(StudentEnrollment.division, StudentEnrollment.roll_number, StudentEnrollment.student_id)).scalars().all()


def _resolve_attendance_enrollment(values, roster, verified_prns):
    candidate_sets = []
    student_code = (values.get("student_code") or "").strip().upper()
    roll_number = (values.get("roll_number") or "").strip().upper()
    prn = "".join((values.get("prn") or "").upper().split())
    if student_code:
        matches = {e.id for e in roster if e.student.student_code.upper() == student_code}
        if not matches: return None, "Student code did not match this attendance session roster."
        candidate_sets.append(matches)
    if roll_number:
        matches = {e.id for e in roster if (e.roll_number or "").strip().upper() == roll_number}
        if not matches: return None, "Roll number did not match this attendance session roster."
        candidate_sets.append(matches)
    if prn:
        student_ids = verified_prns.get(prn, set())
        matches = {e.id for e in roster if e.student_id in student_ids}
        if not matches: return None, "PRN did not match a verified PRN on this attendance session roster."
        candidate_sets.append(matches)
    if not candidate_sets: return None, "Provide student_code, roll_number or a verified PRN."
    intersection = set.intersection(*candidate_sets)
    if len(intersection) != 1:
        return None, "Identity columns are ambiguous or point to different students; add a stable student_code or verified PRN."
    return next(iter(intersection)), None


def _attendance_import_preview(session, parsed_records):
    roster = _roster(session)
    student_ids = [e.student_id for e in roster]
    verified_prns = {}
    if student_ids:
        identifiers = db.session.execute(select(StudentIdentifier).where(StudentIdentifier.student_id.in_(student_ids),
            StudentIdentifier.identifier_type == "prn", StudentIdentifier.verification_status == "verified")).scalars().all()
        for identifier in identifiers:
            verified_prns.setdefault(identifier.normalized_value, set()).add(identifier.student_id)
    existing = {r.enrollment_id: r for r in db.session.execute(select(AttendanceRecord).where(AttendanceRecord.session_id == session.id)).scalars().all()}
    seen = set(); output = []
    for record in parsed_records:
        values = record.get("values", {}); errors = []
        if record.get("parse_error"): errors.append(record["parse_error"])
        enrollment_id, error = _resolve_attendance_enrollment(values, roster, verified_prns)
        if error: errors.append(error)
        if enrollment_id and enrollment_id in seen: errors.append("This enrollment appears more than once in the spreadsheet.")
        if enrollment_id: seen.add(enrollment_id)
        status = (values.get("status") or "").strip().lower()
        status = {"p": "present", "a": "absent", "e": "excused", "x": "excused"}.get(status, status)
        if status not in VALID_ATTENDANCE: errors.append("Status must be present, absent or excused.")
        reason = (values.get("correction_reason") or "").strip()
        old = existing.get(enrollment_id) if enrollment_id else None
        if old and old.status != status and len(reason) < 8:
            errors.append("Changing existing attendance requires correction_reason of at least 8 characters.")
        enrollment = next((e for e in roster if e.id == enrollment_id), None) if enrollment_id else None
        output.append({"row_number": record["row_number"], "student_code": enrollment.student.student_code if enrollment else values.get("student_code", ""),
            "student_name": enrollment.student.full_name if enrollment else "Unmatched", "roll_number": enrollment.roll_number if enrollment else values.get("roll_number", ""),
            "enrollment_id": enrollment_id, "status": status, "correction_reason": reason, "errors": errors, "valid": not errors})
    return output


def _apply_attendance_import(session, rows):
    roster = {e.id: e for e in _roster(session)}
    existing = {r.enrollment_id: r for r in db.session.execute(select(AttendanceRecord).where(AttendanceRecord.session_id == session.id)).scalars().all()}
    changes = 0
    for row in rows:
        enrollment_id = int(row["enrollment_id"]); status = row["status"]
        if enrollment_id not in roster or status not in VALID_ATTENDANCE:
            raise ValueError(f"Row {row['row_number']}: the enrollment/status is no longer valid; preview the import again.")
        old = existing.get(enrollment_id)
        if old:
            if old.status == status: continue
            reason = (row.get("correction_reason") or "").strip()
            if len(reason) < 8: raise ValueError(f"Row {row['row_number']}: correction reason is required.")
            before = {"status": old.status}; old.status = status; old.marked_by_id = current_user.id
            _audit("attendance_corrected_by_import", "attendance_record", old.id, before=before, after={"status": status}, reason=reason)
        else:
            record = AttendanceRecord(session_id=session.id, enrollment_id=enrollment_id, status=status, marked_by_id=current_user.id)
            db.session.add(record); db.session.flush()
            _audit("attendance_imported", "attendance_record", record.id, after={"session_id": session.id, "enrollment_id": enrollment_id, "status": status})
        changes += 1
    return changes


@bp.get("/attendance")
@login_required
def index():
    sessions = db.session.execute(select(AttendanceSession).options(selectinload(AttendanceSession.subject))
        .order_by(AttendanceSession.held_on.desc(), AttendanceSession.id.desc()).limit(100)).scalars().all()
    subjects = _subjects()
    return render_template("attendance.html", page_title="Attendance", sessions=sessions, subjects=subjects,
                           current_year=current_app.config["CURRENT_ACADEMIC_YEAR"])


@bp.post("/attendance/sessions/create")
@roles_required("admin", "faculty")
def create_session():
    errors = []
    try:
        subject_id = int(request.form.get("subject_id", ""))
        semester = int(request.form.get("semester_number", ""))
    except ValueError:
        subject_id, semester = 0, 0
    subject = db.session.get(CurriculumSubject, subject_id) if subject_id else None
    academic_year = _academic_year(request.form.get("academic_year"))
    held_on_raw = (request.form.get("held_on") or "").strip()
    try:
        held_on = date.fromisoformat(held_on_raw)
    except ValueError:
        held_on = None
    division = " ".join((request.form.get("division") or "").split()) or None
    batch = " ".join((request.form.get("practical_batch") or "").split()) or None
    topic = " ".join((request.form.get("topic") or "").split()) or None
    if not subject or subject.review_status != "verified" or subject.curriculum_version.status not in ("approved", "active"):
        errors.append("Choose a verified subject from an approved/active curriculum.")
    if not academic_year:
        errors.append("Academic year must use YYYY-YY format.")
    if not subject or semester != subject.semester_number:
        errors.append("Semester must match the selected subject.")
    if held_on is None:
        errors.append("Choose a valid session date.")
    if division and len(division) > 16:
        errors.append("Division must be 16 characters or fewer.")
    if batch and len(batch) > 24:
        errors.append("Practical batch must be 24 characters or fewer.")
    if topic and len(topic) > 220:
        errors.append("Topic must be 220 characters or fewer.")
    if errors:
        for item in errors: flash(item, "error")
        return redirect(url_for("attendance.index"))
    session = AttendanceSession(curriculum_subject_id=subject.id, academic_year=academic_year, semester_number=semester,
        held_on=held_on, division=division, practical_batch=batch, topic=topic, created_by_id=current_user.id)
    db.session.add(session)
    try:
        db.session.flush()
        _audit("attendance_session_created", "attendance_session", session.id,
            after={"subject_id": subject.id, "academic_year": academic_year, "semester": semester,
                    "held_on": held_on.isoformat(), "division": division, "practical_batch": batch})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The attendance session conflicts with existing data. No change was saved.", "error")
        return redirect(url_for("attendance.index"))
    flash("Attendance session created. Mark the roster and save.", "success")
    return redirect(url_for("attendance.session_detail", session_id=session.id))


@bp.route("/attendance/sessions/<int:session_id>", methods=["GET", "POST"])
@roles_required("admin", "faculty")
def session_detail(session_id):
    session = db.get_or_404(AttendanceSession, session_id)
    roster = _roster(session)
    existing = {r.enrollment_id: r for r in db.session.execute(select(AttendanceRecord).where(AttendanceRecord.session_id == session.id)).scalars()}
    if request.method == "POST":
        errors = []
        changes = 0
        for enrollment in roster:
            status = (request.form.get(f"attendance_{enrollment.id}") or "").strip().lower()
            if status not in VALID_ATTENDANCE:
                errors.append(f"Choose present, absent or excused for {enrollment.student.full_name}.")
                continue
            old = existing.get(enrollment.id)
            reason = (request.form.get(f"reason_{enrollment.id}") or "").strip()
            if old and old.status != status and len(reason) < 8:
                errors.append(f"A correction reason of at least 8 characters is required for {enrollment.student.full_name}.")
        if errors:
            for item in errors[:12]: flash(item, "error")
            return redirect(url_for("attendance.session_detail", session_id=session.id))
        try:
            for enrollment in roster:
                status = request.form.get(f"attendance_{enrollment.id}").strip().lower()
                old = existing.get(enrollment.id)
                if old:
                    if old.status == status:
                        continue
                    before = {"status": old.status}
                    reason = (request.form.get(f"reason_{enrollment.id}") or "").strip()
                    if len(reason) < 8:
                        raise ValueError("Attendance correction requires a reason of at least 8 characters.")
                    old.status = status
                    old.marked_by_id = current_user.id
                    _audit("attendance_corrected", "attendance_record", old.id, before=before, after={"status": status},
                           reason=reason)
                else:
                    record = AttendanceRecord(session_id=session.id, enrollment_id=enrollment.id, status=status, marked_by_id=current_user.id)
                    db.session.add(record)
                    db.session.flush()
                    _audit("attendance_marked", "attendance_record", record.id, after={"session_id": session.id,
                           "enrollment_id": enrollment.id, "status": status})
                changes += 1
            db.session.commit()
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            flash(f"Attendance was not saved. {exc}", "error")
            return redirect(url_for("attendance.session_detail", session_id=session.id))
        flash(f"Saved {changes} attendance change(s).", "success")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    rows = [{"enrollment": enrollment, "record": existing.get(enrollment.id)} for enrollment in roster]
    return render_template("attendance_detail.html", page_title="Mark attendance", session=session, roster_rows=rows)


@bp.post("/attendance/sessions/<int:session_id>/import-preview")
@roles_required("admin", "faculty")
def import_preview(session_id):
    session = db.get_or_404(AttendanceSession, session_id)
    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose a CSV or XLSX attendance file.", "error")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    raw = upload.stream.read(16 * 1024 * 1024 + 1)
    try:
        parsed = parse_spreadsheet(raw, upload.filename, workflow="attendance")
        rows = _attendance_import_preview(session, parsed)
        token, payload = store_import_preview(upload_root=current_app.config["UPLOAD_ROOT"],
            workflow="attendance", target_id=session.id, actor_id=current_user.id, filename=upload.filename, content=raw, rows=rows)
    except SpreadsheetImportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    payload["can_commit"] = bool(rows) and all(row["valid"] for row in rows)
    payload["valid_count"] = sum(1 for row in rows if row["valid"])
    payload["error_count"] = sum(len(row["errors"]) for row in rows)
    preview_path = Path(current_app.config["UPLOAD_ROOT"]) / "import_previews" / f"{token}.json"
    preview_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return render_template("spreadsheet_import_preview.html", page_title="Review attendance import", workflow="attendance",
        target=session, rows=rows, payload=payload, token=token, back_url=url_for("attendance.session_detail", session_id=session.id),
        confirm_url=url_for("attendance.confirm_import", session_id=session.id, token=token))


@bp.post("/attendance/sessions/<int:session_id>/import-confirm/<token>")
@roles_required("admin", "faculty")
def confirm_import(session_id, token):
    session = db.get_or_404(AttendanceSession, session_id)
    try:
        preview_path, payload = read_import_preview(upload_root=current_app.config["UPLOAD_ROOT"], token=token,
            workflow="attendance", target_id=session.id, actor_id=current_user.id)
    except SpreadsheetImportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    if any(row.get("errors") for row in payload.get("rows", [])) or not payload.get("rows"):
        flash("This preview contains errors. Fix the spreadsheet and preview it again; no changes were saved.", "error")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    try:
        changes = _apply_attendance_import(session, payload["rows"])
        db.session.add(AuditEvent(actor_id=current_user.id, action="attendance_spreadsheet_import_committed",
            entity_type="attendance_session", entity_id=str(session.id),
            after_json={"source_filename": payload["source_filename"], "source_storage_key": payload["source_storage_key"],
                        "sha256": payload["source_sha256"], "data_rows": len(payload["rows"]), "changes": changes}))
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Import was not committed; no partial changes were saved. {exc}", "error")
        return redirect(url_for("attendance.session_detail", session_id=session.id))
    delete_import_preview(preview_path)
    flash(f"Attendance spreadsheet import committed atomically: {changes} record(s) created or corrected. Original file and source hash were retained.", "success")
    return redirect(url_for("attendance.session_detail", session_id=session.id))


@bp.get("/attendance/export.csv")
@login_required
def export_csv():
    rows = db.session.execute(select(AttendanceSession, AttendanceRecord, StudentEnrollment)
        .join(AttendanceRecord, AttendanceRecord.session_id == AttendanceSession.id)
        .join(StudentEnrollment, StudentEnrollment.id == AttendanceRecord.enrollment_id)
        .order_by(AttendanceSession.held_on, StudentEnrollment.roll_number)).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "academic_year", "semester", "subject_code", "subject_title", "roll_number", "division", "batch", "student_code", "student_name", "status"])
    for session, record, enrollment in rows:
        writer.writerow([_safe_csv_value(value) for value in [session.held_on.isoformat(), session.academic_year, session.semester_number, session.subject.subject_code,
            session.subject.title, enrollment.roll_number or "", enrollment.division or "", enrollment.practical_batch or "",
            enrollment.student.student_code, enrollment.student.full_name, record.status]])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="acadpulse-attendance.csv"'
    return response
