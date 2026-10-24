"""Academic policy governance and protected audit log."""
import json
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import AcademicPolicy, AcademicProgram, AuditEvent, CurriculumVersion
from ..services.legacy_mark_service import LEGACY_PROFILE_ID
from ..services.security import roles_required

bp = Blueprint("system", __name__)
POLICY_TYPES = {"grading", "credits", "backlog", "promotion", "supplementary", "attendance"}


def _audit(action, entity_type, entity_id, before=None, after=None, reason=None):
    db.session.add(AuditEvent(actor_id=current_user.id, action=action, entity_type=entity_type, entity_id=str(entity_id),
        before_json=before, after_json=after, reason=reason))


@bp.get("/settings/policies")
@roles_required("admin")
def policies():
    rows = db.session.execute(select(AcademicPolicy).order_by(AcademicPolicy.status, AcademicPolicy.policy_type, AcademicPolicy.policy_code)).scalars().all()
    programs = db.session.execute(select(AcademicProgram).order_by(AcademicProgram.name)).scalars().all()
    versions = db.session.execute(select(CurriculumVersion).order_by(CurriculumVersion.version_code)).scalars().all()
    return render_template("policies.html", page_title="Academic policies", policies=rows, programs=programs, versions=versions,
        policy_types=sorted(POLICY_TYPES), legacy_profile_id=LEGACY_PROFILE_ID)


@bp.post("/settings/policies/create")
@roles_required("admin")
def create_policy():
    code = (request.form.get("policy_code") or "").strip().upper()
    policy_type = (request.form.get("policy_type") or "").strip().lower()
    raw_rules = (request.form.get("rules_json") or "").strip() or "{}"
    source_reference = (request.form.get("source_reference") or "").strip() or None
    try:
        rules = json.loads(raw_rules)
        if not isinstance(rules, dict):
            raise ValueError("Rules must be a JSON object.")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(f"Invalid rules JSON: {exc}", "error")
        return redirect(url_for("system.policies"))
    try:
        program_id = int(request.form.get("program_id")) if request.form.get("program_id") else None
        version_id = int(request.form.get("curriculum_version_id")) if request.form.get("curriculum_version_id") else None
        cohort = int(request.form.get("effective_from_cohort_year")) if request.form.get("effective_from_cohort_year") else None
    except ValueError:
        flash("Programme, version or cohort year selection is invalid.", "error")
        return redirect(url_for("system.policies"))
    if not code or len(code) > 64 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in code):
        flash("Policy code is required and may use letters, numbers, underscores and hyphens.", "error")
        return redirect(url_for("system.policies"))
    if policy_type not in POLICY_TYPES:
        flash("Choose a valid policy type.", "error")
        return redirect(url_for("system.policies"))
    program = db.session.get(AcademicProgram, program_id) if program_id else None
    version = db.session.get(CurriculumVersion, version_id) if version_id else None
    if program_id and not program:
        flash("Selected programme does not exist.", "error")
        return redirect(url_for("system.policies"))
    if version_id and (not version or (program and version.program_id != program.id)):
        flash("Selected curriculum version is invalid or belongs to another programme.", "error")
        return redirect(url_for("system.policies"))
    if cohort is not None and not 1990 <= cohort <= 2200:
        flash("Effective cohort year must be between 1990 and 2200.", "error")
        return redirect(url_for("system.policies"))
    if source_reference and len(source_reference) > 255:
        flash("Source reference must be at most 255 characters.", "error")
        return redirect(url_for("system.policies"))
    policy = AcademicPolicy(policy_code=code, policy_type=policy_type, program_id=program_id,
        curriculum_version_id=version_id, effective_from_cohort_year=cohort, status="draft",
        rules_json=rules, source_reference=source_reference)
    db.session.add(policy)
    try:
        db.session.flush()
        _audit("academic_policy_created", "academic_policy", policy.id, after={"code": code, "type": policy_type, "status": "draft", "source_reference": source_reference})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That policy code already exists.", "error")
        return redirect(url_for("system.policies"))
    flash("Policy draft created. Add the supporting source and approval note before activating it.", "success")
    return redirect(url_for("system.policies"))


@bp.post("/settings/policies/<int:policy_id>/activate")
@roles_required("admin")
def activate_policy(policy_id):
    policy = db.get_or_404(AcademicPolicy, policy_id)
    source_reference = (request.form.get("source_reference") or policy.source_reference or "").strip()
    approval_note = (request.form.get("approval_note") or "").strip()
    if not source_reference:
        flash("A source reference is required before activating a policy.", "error")
        return redirect(url_for("system.policies"))
    if len(approval_note) < 20:
        flash("Add an approval note of at least 20 characters, recording the authority/approval basis.", "error")
        return redirect(url_for("system.policies"))
    if not isinstance(policy.rules_json, dict) or not policy.rules_json:
        flash("This policy has no configured rule set.", "error")
        return redirect(url_for("system.policies"))
    if policy.policy_type == "grading" and policy.rules_json.get("calculation_profile") != LEGACY_PROFILE_ID:
        flash(f"Grading policy must explicitly map calculation_profile to {LEGACY_PROFILE_ID} before activation.", "error")
        return redirect(url_for("system.policies"))
    # Keep only one active policy for this exact scope, avoiding ambiguous selection.
    previous = db.session.execute(select(AcademicPolicy).where(
        AcademicPolicy.policy_type == policy.policy_type, AcademicPolicy.status == "active",
        AcademicPolicy.program_id == policy.program_id, AcademicPolicy.curriculum_version_id == policy.curriculum_version_id,
        AcademicPolicy.effective_from_cohort_year == policy.effective_from_cohort_year,
        AcademicPolicy.id != policy.id)).scalars().all()
    for old in previous:
        old.status = "retired"
        _audit("academic_policy_retired_by_replacement", "academic_policy", old.id, before={"status": "active"},
               after={"status": "retired"}, reason=f"Replaced by {policy.policy_code}.")
    old_status = policy.status
    policy.status = "active"
    policy.source_reference = source_reference
    policy.approval_note = approval_note
    policy.approved_by_id = current_user.id
    policy.approved_at = datetime.now(timezone.utc)
    _audit("academic_policy_activated", "academic_policy", policy.id, before={"status": old_status},
           after={"status": "active", "source_reference": source_reference, "rules_json": policy.rules_json}, reason=approval_note)
    db.session.commit()
    flash("Policy activated. Automatic calculations may use it only where the policy scope and required profile match.", "success")
    return redirect(url_for("system.policies"))


@bp.post("/settings/policies/<int:policy_id>/retire")
@roles_required("admin")
def retire_policy(policy_id):
    policy = db.get_or_404(AcademicPolicy, policy_id)
    if policy.status != "active":
        flash("Only active policies can be retired.", "error")
        return redirect(url_for("system.policies"))
    policy.status = "retired"
    _audit("academic_policy_retired", "academic_policy", policy.id, before={"status": "active"}, after={"status": "retired"})
    db.session.commit()
    flash("Policy retired. Historical calculations remain intact.", "success")
    return redirect(url_for("system.policies"))


@bp.get("/system/audit")
@roles_required("admin")
def audit_log():
    search = (request.args.get("q") or "").strip()
    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.desc())
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(AuditEvent.action.ilike(like), AuditEvent.entity_type.ilike(like), AuditEvent.entity_id.ilike(like), AuditEvent.reason.ilike(like)))
    events = db.session.execute(stmt.limit(300)).scalars().all()
    return render_template("audit_log.html", page_title="Audit trail", events=events, search=search)
