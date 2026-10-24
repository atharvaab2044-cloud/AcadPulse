"""Versioned elective groups, choices and explicit approval."""
import re
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import AuditEvent, CurriculumSubject, CurriculumVersion, ElectiveGroup, ElectiveOption, StudentElectiveSelection, StudentEnrollment
from ..services.security import roles_required

bp = Blueprint("electives", __name__, url_prefix="/electives")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{1,39}$")


def _audit(action, entity_type, entity_id, before=None, after=None, reason=None):
    db.session.add(AuditEvent(actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id), before_json=before, after_json=after, reason=reason))


@bp.get("")
@login_required
def index():
    versions = db.session.execute(select(CurriculumVersion).where(CurriculumVersion.status.in_(("draft", "review", "approved", "active")))
        .order_by(CurriculumVersion.effective_cohort_year.desc(), CurriculumVersion.version_code)).scalars().all()
    groups = db.session.execute(select(ElectiveGroup).options(selectinload(ElectiveGroup.options).selectinload(ElectiveOption.subject),
        selectinload(ElectiveGroup.curriculum_version)).order_by(ElectiveGroup.curriculum_version_id, ElectiveGroup.semester_number, ElectiveGroup.title)).scalars().all()
    subjects = db.session.execute(select(CurriculumSubject).join(CurriculumVersion).where(
        CurriculumVersion.status.in_(("draft", "review"))).order_by(CurriculumVersion.version_code, CurriculumSubject.semester_number, CurriculumSubject.subject_code)).scalars().all()
    enrollments = db.session.execute(select(StudentEnrollment).options(selectinload(StudentEnrollment.student), selectinload(StudentEnrollment.curriculum_version))
        .order_by(StudentEnrollment.academic_year.desc(), StudentEnrollment.semester_number.desc()).limit(100)).scalars().all()
    return render_template("electives.html", page_title="Electives", versions=versions, groups=groups, subjects=subjects, enrollments=enrollments)


@bp.post("/groups/create")
@roles_required("admin")
def create_group():
    try:
        version_id = int(request.form.get("curriculum_version_id", ""))
        semester = int(request.form.get("semester_number", ""))
        required = int(request.form.get("required_selections", "1"))
    except ValueError:
        flash("Choose a curriculum version, semester and selection count.", "error")
        return redirect(url_for("electives.index"))
    version = db.session.get(CurriculumVersion, version_id)
    code = (request.form.get("group_code") or "").strip().upper()
    title = " ".join((request.form.get("title") or "").split())
    if not version or version.status != "draft":
        flash("Elective groups can be created only in a draft curriculum version.", "error")
        return redirect(url_for("electives.index"))
    if not CODE_RE.fullmatch(code) or not title or len(title) > 180 or not 1 <= semester <= 12 or not 1 <= required <= 20:
        flash("Check the group code, title, semester and required selection count.", "error")
        return redirect(url_for("electives.index"))
    group = ElectiveGroup(curriculum_version_id=version.id, semester_number=semester, group_code=code, title=title,
                          required_selections=required, status="active", notes=(request.form.get("notes") or "").strip() or None)
    db.session.add(group)
    try:
        db.session.flush()
        _audit("elective_group_created", "elective_group", group.id, after={"version_id": version.id, "semester": semester, "code": code, "title": title})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That elective group code already exists in this curriculum semester.", "error")
        return redirect(url_for("electives.index"))
    flash("Elective group created as part of the curriculum draft.", "success")
    return redirect(url_for("electives.index"))


@bp.post("/groups/<int:group_id>/options/create")
@roles_required("admin")
def add_option(group_id):
    group = db.get_or_404(ElectiveGroup, group_id)
    if group.curriculum_version.status != "draft":
        flash("Options can only be edited while the curriculum version is a draft.", "error")
        return redirect(url_for("electives.index"))
    try:
        subject_id = int(request.form.get("curriculum_subject_id", ""))
    except ValueError:
        subject_id = 0
    subject = db.session.get(CurriculumSubject, subject_id) if subject_id else None
    if not subject or subject.curriculum_version_id != group.curriculum_version_id or subject.semester_number != group.semester_number:
        flash("The subject must belong to the same curriculum version and semester as the elective group.", "error")
        return redirect(url_for("electives.index"))
    option = ElectiveOption(elective_group_id=group.id, curriculum_subject_id=subject.id, is_active=True)
    db.session.add(option)
    try:
        db.session.flush()
        _audit("elective_option_added", "elective_option", option.id, after={"group_id": group.id, "subject_id": subject.id})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That subject is already an option for this group.", "error")
        return redirect(url_for("electives.index"))
    flash("Elective option added.", "success")
    return redirect(url_for("electives.index"))


@bp.post("/enrollments/<int:enrollment_id>/select")
@bp.post("/selections/select")
@roles_required("admin", "faculty")
def select_option(enrollment_id=None):
    if enrollment_id is None:
        try:
            enrollment_id = int(request.form.get("enrollment_id", ""))
        except ValueError:
            enrollment_id = 0
    enrollment = db.get_or_404(StudentEnrollment, enrollment_id)
    try:
        group_id = int(request.form.get("elective_group_id", ""))
        subject_id = int(request.form.get("curriculum_subject_id", ""))
    except ValueError:
        flash("Choose an elective group and option.", "error")
        return redirect(url_for("electives.index"))
    group = db.session.get(ElectiveGroup, group_id)
    option = db.session.scalar(select(ElectiveOption).where(ElectiveOption.elective_group_id == group_id,
        ElectiveOption.curriculum_subject_id == subject_id, ElectiveOption.is_active.is_(True)))
    if not group or not option or group.status != "active" or group.semester_number != enrollment.semester_number:
        flash("That elective option is not valid for this enrollment semester.", "error")
        return redirect(url_for("electives.index"))
    if enrollment.curriculum_version_id != group.curriculum_version_id:
        flash("The elective group must belong to the enrollment's curriculum version.", "error")
        return redirect(url_for("electives.index"))
    selection = StudentElectiveSelection(enrollment_id=enrollment.id, elective_group_id=group.id,
        curriculum_subject_id=subject_id, selection_status="selected", selected_by_id=current_user.id,
        note=(request.form.get("note") or "").strip() or None)
    db.session.add(selection)
    try:
        db.session.flush()
        _audit("elective_selected", "student_elective_selection", selection.id, after={"enrollment_id": enrollment.id, "group_id": group.id, "subject_id": subject_id})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This subject has already been selected in that elective group for this enrollment.", "error")
        return redirect(url_for("electives.index"))
    flash("Elective selection recorded as pending approval.", "success")
    return redirect(url_for("electives.index"))


@bp.post("/selections/<int:selection_id>/approve")
@roles_required("admin")
def approve_selection(selection_id):
    selection = db.get_or_404(StudentElectiveSelection, selection_id)
    if selection.selection_status != "selected":
        flash("Only pending elective selections can be approved.", "error")
        return redirect(url_for("electives.index"))
    group = selection.elective_group
    count = db.session.scalar(select(func.count(StudentElectiveSelection.id)).where(
        StudentElectiveSelection.enrollment_id == selection.enrollment_id,
        StudentElectiveSelection.elective_group_id == group.id,
        StudentElectiveSelection.selection_status == "approved")) or 0
    if count >= group.required_selections:
        flash("The required number of elective selections is already approved for this student/group.", "error")
        return redirect(url_for("electives.index"))
    selection.selection_status = "approved"
    selection.approved_by_id = current_user.id
    selection.approved_at = datetime.now(timezone.utc)
    _audit("elective_selection_approved", "student_elective_selection", selection.id, after={"selection_status": "approved"})
    db.session.commit()
    flash("Elective selection approved.", "success")
    return redirect(url_for("electives.index"))
