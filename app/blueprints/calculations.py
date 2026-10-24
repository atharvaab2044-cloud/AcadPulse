"""Policy-gated SGPA/CGPA snapshots and explicit promotion previews."""
from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    AcademicPolicy, AuditEvent, CalculationSnapshot, ExamAttempt, Student,
    StudentEnrollment,
)
from ..services.academic_calculations import (
    compute_enrollment_sgpa, compute_student_cgpa, evaluate_promotion,
)
from ..services.legacy_mark_service import LEGACY_PROFILE_ID
from ..services.security import roles_required

bp = Blueprint("calculations", __name__, url_prefix="/calculations")


def _audit(action, entity_type, entity_id, *, before=None, after=None, reason=None):
    db.session.add(AuditEvent(
        actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id),
        before_json=before, after_json=after, reason=reason,
    ))


def _decimal_text(value):
    return str(value) if value is not None else None


def _serialize_sgpa_preview(preview):
    """Convert the service result to safe JSON primitives for immutable snapshots."""
    subjects = []
    for item in preview.get("subjects", []):
        attempt = item.get("attempt")
        subject = item["subject"]
        subjects.append({
            "subject_id": subject.id,
            "subject_code": subject.subject_code,
            "subject_title": subject.title,
            "credits": _decimal_text(item.get("credits")),
            "attempt_id": attempt.id if attempt else None,
            "attempt_type": attempt.attempt_type if attempt else None,
            "attempt_status": attempt.official_status if attempt else None,
            "attempt_grade": attempt.official_grade if attempt else None,
            "attempt_total": _decimal_text(attempt.total_marks) if attempt else None,
            "grade_point": _decimal_text(item.get("grade_point")),
            "passed": item.get("is_passed"),
            "grade_source": item.get("grade_source"),
            "issue": item.get("issue"),
        })
    return {
        "status": preview.get("status"),
        "complete": bool(preview.get("complete")),
        "required_subject_count": preview.get("required_subject_count", 0),
        "covered_subject_count": preview.get("covered_subject_count", 0),
        "credits_registered": preview.get("credits_registered", 0),
        "credits_earned": preview.get("credits_earned", 0),
        "subjects": subjects,
        "warnings": list(preview.get("warnings", [])),
        "policy_code": preview.get("policy").policy_code if preview.get("policy") else None,
        "calculation_profile": LEGACY_PROFILE_ID,
    }


def _relevant_enrollment(enrollment_id):
    return db.get_or_404(
        StudentEnrollment,
        enrollment_id,
        options=[
            selectinload(StudentEnrollment.student),
            selectinload(StudentEnrollment.program),
            selectinload(StudentEnrollment.curriculum_version),
        ],
    )


@bp.get("")
@login_required
def index():
    enrollments = db.session.execute(
        select(StudentEnrollment)
        .options(
            selectinload(StudentEnrollment.student),
            selectinload(StudentEnrollment.program),
            selectinload(StudentEnrollment.curriculum_version),
        )
        .where(StudentEnrollment.enrollment_status.in_(("enrolled", "detained", "completed")))
        .order_by(StudentEnrollment.academic_year.desc(), StudentEnrollment.semester_number, StudentEnrollment.id.desc())
        .limit(300)
    ).scalars().all()
    snapshots = db.session.execute(
        select(CalculationSnapshot)
        .where(CalculationSnapshot.calculation_type == "sgpa")
        .order_by(CalculationSnapshot.created_at.desc())
    ).scalars().all()
    latest_by_enrollment = {}
    for snap in snapshots:
        if snap.enrollment_id and snap.enrollment_id not in latest_by_enrollment:
            latest_by_enrollment[snap.enrollment_id] = snap
    return render_template(
        "calculations.html", page_title="Calculations & promotion",
        enrollments=enrollments, latest_by_enrollment=latest_by_enrollment,
    )


@bp.get("/enrollments/<int:enrollment_id>")
@login_required
def enrollment_detail(enrollment_id):
    enrollment = _relevant_enrollment(enrollment_id)
    preview = compute_enrollment_sgpa(enrollment)
    history = db.session.execute(
        select(CalculationSnapshot)
        .where(CalculationSnapshot.enrollment_id == enrollment.id,
               CalculationSnapshot.calculation_type == "sgpa")
        .order_by(CalculationSnapshot.created_at.desc())
        .limit(30)
    ).scalars().all()
    return render_template(
        "calculation_detail.html", page_title="SGPA calculation",
        enrollment=enrollment, preview=preview, history=history,
    )


@bp.post("/enrollments/<int:enrollment_id>/snapshot")
@roles_required("admin", "faculty")
def create_sgpa_snapshot(enrollment_id):
    enrollment = _relevant_enrollment(enrollment_id)
    preview = compute_enrollment_sgpa(enrollment)
    serialized = _serialize_sgpa_preview(preview)

    # Approved snapshots are never overwritten. Only earlier unapproved snapshots
    # can be superseded by a new calculation run.
    prior = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.enrollment_id == enrollment.id,
        CalculationSnapshot.calculation_type == "sgpa",
        CalculationSnapshot.status.in_(("provisional", "policy_pending", "calculated")),
    ).order_by(CalculationSnapshot.created_at.desc())).scalars().all()
    supersedes = prior[0] if prior else None
    for old in prior:
        old.status = "superseded"

    policy = preview.get("policy")
    status = preview.get("status")
    source_kind = "policy_calculated" if policy and status == "calculated" else "provisional"
    snapshot = CalculationSnapshot(
        student_id=enrollment.student_id, enrollment_id=enrollment.id,
        policy_id=policy.id if policy and status == "calculated" else None,
        supersedes_snapshot_id=supersedes.id if supersedes else None,
        calculation_type="sgpa", source_kind=source_kind,
        academic_year=enrollment.academic_year, semester_number=enrollment.semester_number,
        value_numeric=preview.get("sgpa"),
        value_label=(f"SGPA {preview['sgpa']}" if preview.get("sgpa") is not None else "Pending"),
        inputs_snapshot={"enrollment_id": enrollment.id, "student_id": enrollment.student_id,
                         "curriculum_version_id": enrollment.curriculum_version_id,
                         "calculation_profile": LEGACY_PROFILE_ID},
        details_json=serialized, status=status,
        created_by_id=current_user.id,
        reason=("Generated from current verified effective attempts; inspect detail and warnings before approval."),
    )
    db.session.add(snapshot)
    db.session.flush()
    _audit("sgpa_snapshot_created", "calculation_snapshot", snapshot.id,
           after={"enrollment_id": enrollment.id, "status": status,
                  "value": _decimal_text(snapshot.value_numeric), "policy_id": snapshot.policy_id,
                  "complete": serialized["complete"]},
           reason="Calculation output saved with input provenance.")
    db.session.commit()
    if status == "calculated":
        flash("SGPA snapshot calculated. An administrator must review and approve it before it can contribute to CGPA.", "success")
    elif status == "policy_pending":
        flash("A preview snapshot was saved, but policy approval/profile mapping is missing. It is not authoritative.", "warning")
    else:
        flash("A provisional snapshot was saved with warnings for missing or incomplete results.", "warning")
    return redirect(url_for("calculations.enrollment_detail", enrollment_id=enrollment.id))


@bp.post("/snapshots/<int:snapshot_id>/approve")
@roles_required("admin")
def approve_snapshot(snapshot_id):
    snapshot = db.get_or_404(CalculationSnapshot, snapshot_id)
    if snapshot.status != "calculated":
        flash("Only a completed calculated snapshot can be approved. Pending and provisional snapshots remain non-authoritative.", "error")
        return redirect(_snapshot_destination(snapshot))
    details = snapshot.details_json or {}
    if snapshot.calculation_type == "sgpa":
        policy = db.session.get(AcademicPolicy, snapshot.policy_id) if snapshot.policy_id else None
        enrollment = db.session.get(StudentEnrollment, snapshot.enrollment_id) if snapshot.enrollment_id else None
        policy_scope_matches = bool(policy and enrollment and
            (policy.program_id is None or policy.program_id == enrollment.program_id) and
            (policy.curriculum_version_id is None or policy.curriculum_version_id == enrollment.curriculum_version_id) and
            (policy.effective_from_cohort_year is None or policy.effective_from_cohort_year == enrollment.curriculum_cohort_year))
        if not policy or policy.status != "active" or policy.policy_type != "grading" or (policy.rules_json or {}).get("calculation_profile") != LEGACY_PROFILE_ID or not policy_scope_matches:
            flash("The matching active grading policy is no longer available for this enrollment. Recalculate under an applicable approved policy.", "error")
            return redirect(_snapshot_destination(snapshot))
        if not details.get("complete"):
            flash("The snapshot does not contain complete verified subject coverage.", "error")
            return redirect(_snapshot_destination(snapshot))
    elif snapshot.calculation_type == "cgpa":
        source_ids = details.get("source_sgpa_snapshot_ids") or []
        if not details.get("all_sgpa_approved") or not source_ids:
            flash("CGPA cannot be approved unless all included semester SGPA snapshots were approved.", "error")
            return redirect(url_for("calculations.student_cgpa", student_id=snapshot.student_id))
        current_cgpa = compute_student_cgpa(snapshot.student_id)
        current_source_ids = sorted(row.id for row in current_cgpa.get("used_snapshots", []))
        if current_cgpa.get("cgpa") is None or current_source_ids != sorted(source_ids):
            flash("The approved SGPA source set changed after this CGPA snapshot was calculated. Recalculate CGPA before approval.", "error")
            return redirect(url_for("calculations.student_cgpa", student_id=snapshot.student_id))
    elif snapshot.calculation_type == "promotion":
        policy = db.session.get(AcademicPolicy, snapshot.policy_id) if snapshot.policy_id else None
        enrollment = db.session.get(StudentEnrollment, snapshot.enrollment_id) if snapshot.enrollment_id else None
        policy_scope_matches = bool(policy and enrollment and
            (policy.program_id is None or policy.program_id == enrollment.program_id) and
            (policy.curriculum_version_id is None or policy.curriculum_version_id == enrollment.curriculum_version_id) and
            (policy.effective_from_cohort_year is None or policy.effective_from_cohort_year == enrollment.curriculum_cohort_year))
        if not policy or policy.status != "active" or policy.policy_type != "promotion" or not policy_scope_matches:
            flash("The applicable active promotion policy is no longer available for this enrollment. Re-evaluate promotion.", "error")
            return redirect(url_for("calculations.promotion_detail", enrollment_id=snapshot.enrollment_id))
        if details.get("decision") not in {"eligible", "not_eligible"}:
            flash("This promotion snapshot does not contain a conclusive decision.", "error")
            return redirect(url_for("calculations.promotion_detail", enrollment_id=snapshot.enrollment_id))
        latest_evaluation = evaluate_promotion(enrollment)
        if latest_evaluation.get("policy") is None or latest_evaluation["policy"].id != policy.id or latest_evaluation.get("status") != details.get("decision"):
            flash("The policy or source records changed after this promotion snapshot was calculated. Re-evaluate before approval.", "error")
            return redirect(url_for("calculations.promotion_detail", enrollment_id=snapshot.enrollment_id))
    else:
        flash("This calculation type is not eligible for approval through this workflow.", "error")
        return redirect(url_for("calculations.index"))

    # Keep a single current approved snapshot per calculation scope. Previous
    # approved outputs are marked superseded, not deleted, and their audit history remains.
    if snapshot.calculation_type == "sgpa":
        old_approved = db.session.execute(select(CalculationSnapshot).where(
            CalculationSnapshot.enrollment_id == snapshot.enrollment_id,
            CalculationSnapshot.calculation_type == "sgpa", CalculationSnapshot.status == "approved",
            CalculationSnapshot.id != snapshot.id,
        )).scalars().all()
    elif snapshot.calculation_type == "cgpa":
        old_approved = db.session.execute(select(CalculationSnapshot).where(
            CalculationSnapshot.student_id == snapshot.student_id,
            CalculationSnapshot.calculation_type == "cgpa", CalculationSnapshot.status == "approved",
            CalculationSnapshot.id != snapshot.id,
        )).scalars().all()
    else:
        old_approved = db.session.execute(select(CalculationSnapshot).where(
            CalculationSnapshot.enrollment_id == snapshot.enrollment_id,
            CalculationSnapshot.calculation_type == snapshot.calculation_type,
            CalculationSnapshot.status == "approved", CalculationSnapshot.id != snapshot.id,
        )).scalars().all()
    for old in old_approved:
        previous_status = old.status
        old.status = "superseded"
        _audit("calculation_snapshot_superseded", "calculation_snapshot", old.id,
               before={"status": previous_status}, after={"status": "superseded"},
               reason=f"Superseded by newly approved snapshot {snapshot.id}; source snapshot retained.")

    before = {"status": snapshot.status, "approved_by_id": snapshot.approved_by_id}
    snapshot.status = "approved"
    snapshot.approved_by_id = current_user.id
    snapshot.approved_at = datetime.now(timezone.utc)
    _audit("calculation_snapshot_approved", "calculation_snapshot", snapshot.id,
           before=before, after={"status": "approved", "approved_by_id": current_user.id})
    db.session.commit()
    flash("Calculation snapshot approved and retained with its source inputs.", "success")
    return redirect(_snapshot_destination(snapshot))


def _snapshot_destination(snapshot):
    if snapshot.calculation_type == "sgpa" and snapshot.enrollment_id:
        return url_for("calculations.enrollment_detail", enrollment_id=snapshot.enrollment_id)
    if snapshot.calculation_type == "cgpa":
        return url_for("calculations.student_cgpa", student_id=snapshot.student_id)
    if snapshot.calculation_type == "promotion" and snapshot.enrollment_id:
        return url_for("calculations.promotion_detail", enrollment_id=snapshot.enrollment_id)
    return url_for("calculations.index")


@bp.get("/students/<int:student_id>/cgpa")
@login_required
def student_cgpa(student_id):
    student = db.get_or_404(Student, student_id)
    result = compute_student_cgpa(student_id)
    history = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.student_id == student_id, CalculationSnapshot.calculation_type == "cgpa"
    ).order_by(CalculationSnapshot.created_at.desc()).limit(20)).scalars().all()
    return render_template("cgpa_detail.html", page_title="CGPA calculation", student=student, result=result, history=history)


@bp.post("/students/<int:student_id>/cgpa/snapshot")
@roles_required("admin", "faculty")
def create_cgpa_snapshot(student_id):
    student = db.get_or_404(Student, student_id)
    result = compute_student_cgpa(student_id)
    if result.get("cgpa") is None:
        flash("CGPA remains pending. Ensure every recorded applicable enrollment has an approved SGPA snapshot and valid credit totals.", "warning")
        return redirect(url_for("calculations.student_cgpa", student_id=student.id))
    prior = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.student_id == student.id, CalculationSnapshot.calculation_type == "cgpa",
        CalculationSnapshot.status.in_(("provisional", "policy_pending", "calculated"))
    ).order_by(CalculationSnapshot.created_at.desc())).scalars().all()
    supersedes = prior[0] if prior else None
    for old in prior:
        old.status = "superseded"
    source_ids = sorted(snap.id for snap in result["used_snapshots"])
    snapshot = CalculationSnapshot(
        student_id=student.id, calculation_type="cgpa", source_kind="policy_calculated", status="calculated",
        value_numeric=result["cgpa"], value_label=f"CGPA {result['cgpa']}",
        inputs_snapshot={"student_id": student.id, "source_sgpa_snapshot_ids": source_ids},
        details_json={"all_sgpa_approved": True, "source_sgpa_snapshot_ids": source_ids,
                      "enrollment_count": len(result["enrollments"]), "calculation_profile": LEGACY_PROFILE_ID},
        supersedes_snapshot_id=supersedes.id if supersedes else None, created_by_id=current_user.id,
        reason="Credit-weighted cumulative value derived only from approved SGPA snapshots.",
    )
    db.session.add(snapshot)
    db.session.flush()
    _audit("cgpa_snapshot_created", "calculation_snapshot", snapshot.id,
           after={"student_id": student.id, "value": str(snapshot.value_numeric), "status": snapshot.status},
           reason="Derived from approved semester SGPA snapshots.")
    db.session.commit()
    flash("CGPA snapshot calculated. Administrator approval is required before treating it as the approved cumulative value.", "success")
    return redirect(url_for("calculations.student_cgpa", student_id=student.id))


@bp.get("/enrollments/<int:enrollment_id>/promotion")
@login_required
def promotion_detail(enrollment_id):
    enrollment = _relevant_enrollment(enrollment_id)
    result = evaluate_promotion(enrollment)
    history = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.enrollment_id == enrollment.id,
        CalculationSnapshot.calculation_type == "promotion"
    ).order_by(CalculationSnapshot.created_at.desc()).limit(20)).scalars().all()
    return render_template("promotion_detail.html", page_title="Promotion eligibility preview",
                           enrollment=enrollment, result=result, history=history)


@bp.post("/enrollments/<int:enrollment_id>/promotion/snapshot")
@roles_required("admin", "faculty")
def create_promotion_snapshot(enrollment_id):
    enrollment = _relevant_enrollment(enrollment_id)
    result = evaluate_promotion(enrollment)
    decision = result.get("status", "policy_pending")
    prior = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.enrollment_id == enrollment.id, CalculationSnapshot.calculation_type == "promotion",
        CalculationSnapshot.status.in_(("provisional", "policy_pending", "calculated"))
    ).order_by(CalculationSnapshot.created_at.desc())).scalars().all()
    supersedes = prior[0] if prior else None
    for old in prior:
        old.status = "superseded"
    policy = result.get("policy")
    conclusive = decision in {"eligible", "not_eligible"} and policy is not None
    snap_status = "calculated" if conclusive else "policy_pending"
    detail = {key: value for key, value in result.items() if key != "policy"}
    detail.update({"decision": decision, "policy_code": policy.policy_code if policy else None,
                   "policy_id": policy.id if policy else None})
    # Normalize any Decimal outputs before storing JSON.
    for key, value in list(detail.items()):
        if isinstance(value, Decimal):
            detail[key] = str(value)
    snapshot = CalculationSnapshot(
        student_id=enrollment.student_id, enrollment_id=enrollment.id, policy_id=policy.id if conclusive else None,
        supersedes_snapshot_id=supersedes.id if supersedes else None,
        calculation_type="promotion", source_kind="policy_calculated" if conclusive else "provisional",
        academic_year=enrollment.academic_year, semester_number=enrollment.semester_number,
        value_numeric=result.get("credit_percentage"), value_label=decision.replace("_", " ").title(),
        inputs_snapshot={"student_id": enrollment.student_id, "enrollment_id": enrollment.id,
                         "curriculum_version_id": enrollment.curriculum_version_id},
        details_json=detail, status=snap_status, created_by_id=current_user.id,
        reason=result.get("message") or "Promotion evaluation follows the active scoped policy and does not automatically change enrollment status.",
    )
    db.session.add(snapshot)
    db.session.flush()
    _audit("promotion_snapshot_created", "calculation_snapshot", snapshot.id,
           after={"decision": decision, "status": snap_status, "policy_id": snapshot.policy_id})
    db.session.commit()
    flash("Promotion evaluation saved. Enrollment status was not changed automatically.", "success" if conclusive else "warning")
    return redirect(url_for("calculations.promotion_detail", enrollment_id=enrollment.id))
