"""Syllabus upload, draft review, curriculum versioning and activation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import AcademicProgram, AuditEvent, CurriculumScheme, CurriculumSubject, CurriculumVersion
from ..services.curriculum_workflow import (
    COURSE_TYPES,
    curriculum_readiness_errors,
    diff_curriculum_versions,
    normalize_subject_code,
    parse_assessment_structure,
    subject_readiness_errors,
    validate_assessment_structure,
)
from ..services.security import roles_required
from ..services.syllabus_extractor import SyllabusExtractionError, extract_syllabus_pdf

bp = Blueprint("curricula", __name__)
VERSION_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,63}$")
SUBJECT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9./_-]{1,39}$")
SCHEME_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,39}$")
COURSE_TYPE_CHOICES = sorted(COURSE_TYPES)


def _audit(action: str, version: CurriculumVersion, before=None, after=None, reason=None):
    db.session.add(AuditEvent(
        actor_id=current_user.id,
        action=action,
        entity_type="curriculum_version",
        entity_id=str(version.id),
        before_json=before,
        after_json=after,
        reason=reason,
    ))


def _resolve_scheme(form) -> tuple[CurriculumScheme | None, str | None]:
    scheme_id = (form.get("scheme_id") or "").strip()
    if scheme_id:
        try:
            scheme = db.session.get(CurriculumScheme, int(scheme_id))
        except (TypeError, ValueError):
            scheme = None
        if not scheme or not scheme.is_active:
            return None, "Choose an active registered scheme, or enter a new scheme code and name."
        return scheme, None

    code = (form.get("new_scheme_code") or "NEP_MIS").strip().upper()
    name = " ".join((form.get("new_scheme_name") or "NEP/MIS").split())
    if not SCHEME_CODE_RE.fullmatch(code):
        return None, "Scheme code must be 2–40 characters and use letters, numbers, underscores or hyphens."
    if not name or len(name) > 100:
        return None, "Scheme name is required and must be at most 100 characters."
    existing = db.session.scalar(select(CurriculumScheme).where(func.upper(CurriculumScheme.code) == code))
    if existing:
        if not existing.is_active:
            return None, "That scheme code exists but is inactive. Choose another code or have an administrator review it."
        return existing, None
    scheme = CurriculumScheme(code=code, display_name=name, is_active=True)
    db.session.add(scheme)
    db.session.flush()
    return scheme, None


def _make_version_code(requested: str, program: AcademicProgram, scheme: CurriculumScheme, cohort: int) -> tuple[str | None, str | None]:
    requested = (requested or "").strip().upper()
    if requested:
        if not VERSION_CODE_RE.fullmatch(requested):
            return None, "Version code must be 2–64 characters and use letters, numbers, underscores or hyphens."
        exists = db.session.scalar(select(CurriculumVersion.id).where(CurriculumVersion.version_code == requested))
        if exists:
            return None, "That curriculum version code already exists. Choose a unique version code."
        return requested, None

    base = re.sub(r"[^A-Z0-9_-]", "_", f"{program.code}_{scheme.code}_{cohort}")[:55].strip("_-")
    candidate = base
    revision = 2
    while db.session.scalar(select(CurriculumVersion.id).where(CurriculumVersion.version_code == candidate)):
        suffix = f"_R{revision}"
        candidate = f"{base[:64 - len(suffix)]}{suffix}"
        revision += 1
    return candidate, None


def _safe_source_path(version: CurriculumVersion) -> Path:
    upload_root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    source = (upload_root / (version.source_storage_key or "")).resolve()
    if upload_root not in source.parents or not source.is_file():
        abort(404)
    return source


def _build_subject(form, version_id: int, subject: CurriculumSubject | None = None):
    """Validate a subject edit and return candidate values plus an error list."""
    code = normalize_subject_code(form.get("subject_code", ""))
    title = " ".join((form.get("title") or "").split())
    try:
        semester = int(form.get("semester_number", ""))
    except (TypeError, ValueError):
        semester = 0
    raw_credits = (form.get("credits") or "").strip()
    if raw_credits:
        try:
            credits = Decimal(raw_credits)
        except InvalidOperation:
            credits = Decimal("NaN")
    else:
        credits = None
    course_type = (form.get("course_type") or "theory").strip().lower()
    review_status = (form.get("review_status") or "review_required").strip().lower()
    structure, json_error = parse_assessment_structure(form.get("assessment_structure_json", "{}"))
    notes = (form.get("notes") or "").strip() or None
    try:
        source_page = int(form.get("source_page", "")) if (form.get("source_page") or "").strip() else None
    except (TypeError, ValueError):
        source_page = -1

    errors = []
    if not SUBJECT_CODE_RE.fullmatch(code):
        errors.append("Subject code must be 2–40 characters and contain only letters, numbers, dots, slashes, underscores or hyphens.")
    if not title or len(title) > 220:
        errors.append("Subject title is required and must be 220 characters or fewer.")
    if not 1 <= semester <= 12:
        errors.append("Semester must be between 1 and 12.")
    if credits is not None and (not credits.is_finite() or credits < 0 or credits > Decimal("999.99")):
        errors.append("Credits must be from 0 to 999.99, or leave the field blank until confirmed.")
    if course_type not in COURSE_TYPES:
        errors.append("Choose a valid course type.")
    if review_status not in {"review_required", "verified", "rejected"}:
        errors.append("Choose a valid review status.")
    if source_page is not None and not 1 <= source_page <= 1000:
        errors.append("Source page must be between 1 and 1000, or left blank for a manual entry.")
    if json_error:
        errors.append(json_error)

    if structure is not None and review_status == "verified":
        if credits is None:
            errors.append("Credits must be confirmed before a subject can be marked verified.")
        errors.extend(validate_assessment_structure(structure))

    duplicate_query = select(CurriculumSubject.id).where(
        CurriculumSubject.curriculum_version_id == version_id,
        CurriculumSubject.semester_number == semester,
        func.upper(CurriculumSubject.subject_code) == code,
    )
    if subject:
        duplicate_query = duplicate_query.where(CurriculumSubject.id != subject.id)
    if code and db.session.scalar(duplicate_query):
        errors.append("That subject code already exists in this semester of the version.")

    values = {
        "subject_code": code,
        "title": title,
        "semester_number": semester,
        "credits": credits,
        "course_type": course_type,
        "assessment_structure": structure or {},
        "review_status": review_status,
        "notes": notes,
        "source_page": source_page,
    }
    return values, errors


def _render_subject_form(version, form_data=None, subject=None, status=200):
    if subject and not form_data:
        form_data = {
            "subject_code": subject.subject_code,
            "title": subject.title,
            "semester_number": str(subject.semester_number),
            "credits": str(subject.credits) if subject.credits is not None else "",
            "course_type": subject.course_type,
            "assessment_structure_json": json.dumps(subject.assessment_structure or {}, indent=2, ensure_ascii=False),
            "review_status": subject.review_status,
            "notes": subject.notes or "",
            "source_page": str(subject.source_page or ""),
        }
    elif not form_data:
        form_data = {
            "semester_number": "1",
            "course_type": "theory",
            "assessment_structure_json": json.dumps({"components": []}, indent=2),
            "review_status": "review_required",
        }
    return render_template(
        "curriculum_subject_form.html",
        page_title="Edit curriculum subject" if subject else "Add curriculum subject",
        version=version,
        subject=subject,
        form_data=form_data,
        course_types=COURSE_TYPE_CHOICES,
    ), status


@bp.post("/curriculum/upload")
@roles_required("admin")
def upload():
    upload_file = request.files.get("syllabus_pdf")
    if not upload_file or not upload_file.filename:
        flash("Choose a syllabus PDF to upload.", "error")
        return redirect(url_for("main.curriculum"))
    original_name = secure_filename(upload_file.filename)[:255]
    if not original_name or not original_name.lower().endswith(".pdf"):
        flash("Only PDF syllabus files are accepted.", "error")
        return redirect(url_for("main.curriculum"))

    max_size = int(current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
    content = upload_file.stream.read(max_size + 1)
    if len(content) > max_size:
        flash(f"The syllabus exceeds the {max_size // (1024 * 1024)} MB upload limit.", "error")
        return redirect(url_for("main.curriculum"))
    try:
        extraction = extract_syllabus_pdf(content)
    except SyllabusExtractionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.curriculum"))
    digest = hashlib.sha256(content).hexdigest()

    try:
        program_id = int(request.form.get("program_id", ""))
        cohort = int(request.form.get("effective_cohort_year", ""))
    except (TypeError, ValueError):
        flash("Choose a programme and enter a valid curriculum cohort year.", "error")
        return redirect(url_for("main.curriculum"))
    program = db.session.get(AcademicProgram, program_id)
    if not program or not program.is_active:
        flash("Choose an active programme before uploading a syllabus.", "error")
        return redirect(url_for("main.curriculum"))
    if not 1990 <= cohort <= 2200:
        flash("Curriculum cohort year must be between 1990 and 2200.", "error")
        return redirect(url_for("main.curriculum"))

    try:
        scheme, scheme_error = _resolve_scheme(request.form)
    except IntegrityError:
        db.session.rollback()
        flash("Scheme code conflicts with another change. Reload academic setup and retry.", "error")
        return redirect(url_for("main.curriculum"))
    if scheme_error:
        db.session.rollback()
        flash(scheme_error, "error")
        return redirect(url_for("main.curriculum"))

    duplicate = db.session.scalar(select(CurriculumVersion).where(
        CurriculumVersion.program_id == program.id,
        CurriculumVersion.scheme_id == scheme.id,
        CurriculumVersion.effective_cohort_year == cohort,
        CurriculumVersion.source_sha256 == digest,
    ))
    if duplicate:
        db.session.rollback()
        flash(f"This exact PDF already exists for that programme, scheme and cohort as {duplicate.version_code}. No duplicate version was created.", "info")
        return redirect(url_for("curricula.detail", version_id=duplicate.id))

    requested_code = request.form.get("version_code", "")
    version_code, code_error = _make_version_code(requested_code, program, scheme, cohort)
    if code_error:
        db.session.rollback()
        flash(code_error, "error")
        return redirect(url_for("main.curriculum"))

    compare_version_id = request.form.get("compare_version_id", "").strip()
    compare_version = None
    if compare_version_id:
        try:
            compare_version = db.session.get(CurriculumVersion, int(compare_version_id))
        except (TypeError, ValueError):
            compare_version = None
        if not compare_version or compare_version.program_id != program.id or compare_version.scheme_id != scheme.id:
            db.session.rollback()
            flash("Choose a comparison version from the same programme and scheme, or leave comparison blank.", "error")
            return redirect(url_for("main.curriculum"))

    storage_folder = Path(current_app.config["UPLOAD_ROOT"]).resolve() / "syllabus"
    storage_folder.mkdir(parents=True, exist_ok=True)
    generated_name = f"{uuid4().hex}.pdf"
    storage_key = f"syllabus/{generated_name}"
    file_path = storage_folder / generated_name

    version = CurriculumVersion(
        version_code=version_code,
        scheme_id=scheme.id,
        program_id=program.id,
        effective_cohort_year=cohort,
        status="draft",
        source_filename=original_name,
        source_sha256=digest,
        source_storage_key=storage_key,
        extracted_at=datetime.now(timezone.utc),
    )
    try:
        file_path.write_bytes(content)
        db.session.add(version)
        db.session.flush()
        inserted = 0
        for candidate in extraction["candidates"]:
            credits = Decimal(candidate["credits"]) if candidate["credits"] is not None else None
            subject = CurriculumSubject(
                curriculum_version_id=version.id,
                subject_code=candidate["subject_code"],
                title=candidate["title"],
                semester_number=candidate["semester_number"],
                credits=credits,
                course_type=candidate["course_type"],
                assessment_structure=candidate["assessment_structure"],
                source_page=candidate["source_page"],
                extraction_confidence=Decimal(str(candidate["extraction_confidence"])),
                review_status="review_required",
                notes="Extracted suggestion. Confirm code, title, semester, credits and assessment structure against the source PDF.",
            )
            db.session.add(subject)
            inserted += 1
        db.session.flush()
        _audit(
            "syllabus_uploaded_curriculum_draft_created", version,
            after={
                "version_code": version.version_code,
                "program_code": program.code,
                "scheme_code": scheme.code,
                "cohort_year": cohort,
                "source_sha256": digest,
                "page_count": extraction["page_count"],
                "candidate_count": inserted,
                "pages_with_text": extraction["pages_with_text"],
                "parser_name": extraction["parser_name"],
            },
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        file_path.unlink(missing_ok=True)
        flash("A unique curriculum or subject record conflicted with an existing record. No draft was saved; check the code and retry.", "error")
        return redirect(url_for("main.curriculum"))
    except Exception:
        db.session.rollback()
        file_path.unlink(missing_ok=True)
        current_app.logger.exception("Syllabus draft creation failed")
        flash("The syllabus draft could not be saved. No curriculum was activated; check the server log and retry.", "error")
        return redirect(url_for("main.curriculum"))

    if extraction["is_likely_scanned"]:
        flash("PDF stored as a draft, but little extractable text was found. This may be a scanned syllabus; add or correct subjects manually. Nothing was activated.", "warning")
    elif inserted == 0:
        flash("PDF stored as a draft, but no subject rows could be confidently suggested. Add subjects manually and review the source PDF.", "warning")
    else:
        flash(f"Draft {version.version_code} created with {inserted} subject suggestions. They are all review-required; assessment components must be checked manually before submission.", "success")
    if extraction["subjects_without_semester_skipped"]:
        flash(f"The extractor skipped {extraction['subjects_without_semester_skipped']} candidate row(s) because no semester could be identified. Add those manually in the correct semester.", "info")
    if extraction["pages_with_little_or_no_text"]:
        flash(f"Pages with little or no selectable text: {', '.join(map(str, extraction['pages_with_little_or_no_text'][:20]))}. Verify these pages manually.", "info")
    if extraction["ambiguous_semester_pages"]:
        flash(f"Semester labels were ambiguous on page(s) {', '.join(map(str, extraction['ambiguous_semester_pages'][:20]))}; subject rows on those pages were not assigned a semester. Add them manually.", "warning")
    if compare_version:
        return redirect(url_for("curricula.detail", version_id=version.id, compare=compare_version.id))
    return redirect(url_for("curricula.detail", version_id=version.id))


@bp.get("/curriculum/<int:version_id>")
@login_required
def detail(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    subjects = db.session.execute(select(CurriculumSubject).where(
        CurriculumSubject.curriculum_version_id == version.id
    ).order_by(CurriculumSubject.semester_number, CurriculumSubject.subject_code)).scalars().all()
    compare_id = request.args.get("compare", type=int)
    comparison = None
    diff = None
    if compare_id:
        comparison = db.session.get(CurriculumVersion, compare_id)
        if comparison and comparison.program_id == version.program_id and comparison.scheme_id == version.scheme_id and comparison.id != version.id:
            diff = diff_curriculum_versions(comparison, version)
        else:
            comparison = None
            flash("Comparison is only available between different versions of the same programme and scheme.", "warning")
    other_versions = db.session.execute(select(CurriculumVersion).where(
        CurriculumVersion.id != version.id,
        CurriculumVersion.program_id == version.program_id,
        CurriculumVersion.scheme_id == version.scheme_id,
    ).order_by(CurriculumVersion.effective_cohort_year.desc(), CurriculumVersion.created_at.desc())).scalars().all()
    readiness = curriculum_readiness_errors(version) if version.status in {"draft", "review", "approved"} else []
    return render_template(
        "curriculum_detail.html",
        page_title=version.version_code,
        version=version,
        subjects=subjects,
        comparison=comparison,
        diff=diff,
        other_versions=other_versions,
        readiness_errors=readiness,
        course_types=COURSE_TYPE_CHOICES,
    )


@bp.get("/curriculum/<int:version_id>/source")
@login_required
def source_pdf(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    path = _safe_source_path(version)
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=version.source_filename or "syllabus.pdf")


@bp.route("/curriculum/<int:version_id>/subjects/new", methods=["GET", "POST"])
@roles_required("admin")
def add_subject(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    if version.status != "draft":
        flash("Only draft curriculum versions can be edited. Create a new revision for changes.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    if request.method == "POST":
        values, errors = _build_subject(request.form, version.id)
        if errors:
            for error in errors:
                flash(error, "error")
            return _render_subject_form(version, request.form, status=400)
        subject = CurriculumSubject(curriculum_version_id=version.id, **values)
        try:
            db.session.add(subject)
            db.session.flush()
            _audit("curriculum_subject_added", version, after={"subject_id": subject.id, "subject_code": subject.subject_code, "semester": subject.semester_number})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That subject conflicts with an existing row in this version and semester.", "error")
            return _render_subject_form(version, request.form, status=409)
        flash("Subject added to the draft. It is not active for enrollment or assessment yet.", "success")
        return redirect(url_for("curricula.detail", version_id=version.id))
    return _render_subject_form(version)


@bp.route("/curriculum/<int:version_id>/subjects/<int:subject_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_subject(version_id: int, subject_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    subject = db.get_or_404(CurriculumSubject, subject_id)
    if subject.curriculum_version_id != version.id:
        abort(404)
    if version.status != "draft":
        flash("Curriculum subjects are read-only after submission. Create a new revision for changes.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    if request.method == "POST":
        values, errors = _build_subject(request.form, version.id, subject)
        if errors:
            for error in errors:
                flash(error, "error")
            return _render_subject_form(version, request.form, subject=subject, status=400)
        before = {
            "subject_code": subject.subject_code,
            "title": subject.title,
            "semester_number": subject.semester_number,
            "credits": str(subject.credits) if subject.credits is not None else None,
            "course_type": subject.course_type,
            "review_status": subject.review_status,
        }
        for key, value in values.items():
            setattr(subject, key, value)
        _audit(
            "curriculum_subject_reviewed", version,
            before=before,
            after={
                "subject_id": subject.id,
                "subject_code": subject.subject_code,
                "title": subject.title,
                "semester_number": subject.semester_number,
                "credits": str(subject.credits) if subject.credits is not None else None,
                "course_type": subject.course_type,
                "review_status": subject.review_status,
            },
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The edited subject conflicts with another row in this version and semester.", "error")
            return _render_subject_form(version, request.form, subject=subject, status=409)
        flash("Subject changes saved. The curriculum remains a draft until separately submitted, approved and activated.", "success")
        return redirect(url_for("curricula.detail", version_id=version.id))
    return _render_subject_form(version, subject=subject)


@bp.post("/curriculum/<int:version_id>/subjects/<int:subject_id>/delete")
@roles_required("admin")
def delete_subject(version_id: int, subject_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    subject = db.get_or_404(CurriculumSubject, subject_id)
    if subject.curriculum_version_id != version.id:
        abort(404)
    if version.status != "draft":
        flash("Only draft subject suggestions can be removed. Submitted and historical versions are preserved.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    before = {"subject_code": subject.subject_code, "semester_number": subject.semester_number, "title": subject.title}
    _audit("curriculum_subject_removed_from_draft", version, before=before, reason="Administrator removed a suggestion before curriculum submission.")
    db.session.delete(subject)
    db.session.commit()
    flash("Subject suggestion removed from the draft. The original PDF remains preserved.", "success")
    return redirect(url_for("curricula.detail", version_id=version.id))


@bp.post("/curriculum/<int:version_id>/submit-review")
@roles_required("admin")
def submit_review(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    if version.status != "draft":
        flash("Only draft curriculum versions can be submitted for review.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    errors = curriculum_readiness_errors(version)
    if errors:
        flash("Curriculum not ready: " + " | ".join(errors[:5]), "error")
        if len(errors) > 5:
            flash(f"There are {len(errors)} subjects or readiness issues to fix. See the readiness checklist below.", "info")
        return redirect(url_for("curricula.detail", version_id=version.id))
    before = {"status": version.status}
    version.status = "review"
    version.submitted_for_review_at = datetime.now(timezone.utc)
    _audit("curriculum_submitted_for_review", version, before=before, after={"status": version.status, "subject_count": len(version.subjects)})
    db.session.commit()
    flash("Curriculum submitted for approval. Its subjects are now read-only; create a new revision for corrections.", "success")
    return redirect(url_for("curricula.detail", version_id=version.id))


@bp.post("/curriculum/<int:version_id>/approve")
@roles_required("admin")
def approve(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    if version.status != "review":
        flash("Only a submitted curriculum can be approved.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    note = " ".join((request.form.get("approval_note") or "").split())
    if len(note) < 10:
        flash("Enter an approval note of at least 10 characters for the audit trail.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    errors = curriculum_readiness_errors(version)
    if errors:
        flash("Approval blocked because readiness checks failed: " + " | ".join(errors[:4]), "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    before = {"status": version.status}
    version.status = "approved"
    version.approved_by_id = current_user.id
    version.approved_at = datetime.now(timezone.utc)
    version.approval_note = note
    _audit("curriculum_version_approved", version, before=before, after={"status": version.status, "approved_by_id": current_user.id}, reason=note)
    db.session.commit()
    flash("Curriculum approved. It is not active until an administrator activates it.", "success")
    return redirect(url_for("curricula.detail", version_id=version.id))


@bp.post("/curriculum/<int:version_id>/activate")
@roles_required("admin")
def activate(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    if version.status != "approved":
        flash("Only an approved curriculum version can be activated.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    errors = curriculum_readiness_errors(version)
    if errors:
        flash("Activation blocked because readiness checks failed: " + " | ".join(errors[:4]), "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    active_versions = db.session.execute(select(CurriculumVersion).where(
        CurriculumVersion.program_id == version.program_id,
        CurriculumVersion.scheme_id == version.scheme_id,
        CurriculumVersion.effective_cohort_year == version.effective_cohort_year,
        CurriculumVersion.status == "active",
        CurriculumVersion.id != version.id,
    )).scalars().all()
    retired_codes = []
    for prior in active_versions:
        prior.status = "retired"
        retired_codes.append(prior.version_code)
        _audit("curriculum_version_retired_by_new_activation", prior, before={"status": "active"}, after={"status": "retired", "replacement_version_id": version.id})
    version.status = "active"
    _audit("curriculum_version_activated", version, before={"status": "approved"}, after={"status": "active", "retired_versions": retired_codes})
    db.session.commit()
    if retired_codes:
        flash(f"{version.version_code} is active. Previous active version(s) retired: {', '.join(retired_codes)}. Their records remain preserved.", "success")
    else:
        flash(f"{version.version_code} is now the active curriculum for this programme, scheme and cohort.", "success")
    return redirect(url_for("curricula.detail", version_id=version.id))


@bp.post("/curriculum/<int:version_id>/reject")
@roles_required("admin")
def reject(version_id: int):
    version = db.get_or_404(CurriculumVersion, version_id)
    if version.status != "review":
        flash("Only a curriculum in review can be rejected.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    reason = " ".join((request.form.get("reason") or "").split())
    if len(reason) < 10:
        flash("Enter a rejection reason of at least 10 characters.", "error")
        return redirect(url_for("curricula.detail", version_id=version.id))
    before = {"status": version.status}
    version.status = "rejected"
    version.approval_note = reason
    _audit("curriculum_version_rejected", version, before=before, after={"status": "rejected"}, reason=reason)
    db.session.commit()
    flash("Curriculum rejected and retained for audit. Upload a new PDF/version to make corrections.", "success")
    return redirect(url_for("curricula.detail", version_id=version.id))
