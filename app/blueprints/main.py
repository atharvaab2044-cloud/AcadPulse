from datetime import datetime
from flask import Blueprint, current_app, jsonify, render_template, redirect, url_for
from flask_login import login_required
from sqlalchemy import func, text

from ..extensions import db
from ..models import (
    AcademicPolicy, AcademicProgram, AuditEvent, CurriculumScheme, CurriculumSubject, CurriculumVersion, ResultImport,
    StagedResult, Student, StudentEnrollment, Assessment, AssessmentMark, AttendanceSession, AttendanceRecord, ExamAttempt,
)

bp = Blueprint("main", __name__)


def _current_term():
    academic_year = current_app.config["CURRENT_ACADEMIC_YEAR"]
    return academic_year


@bp.get("/")
@login_required
def dashboard():
    student_count = db.session.scalar(db.select(func.count(Student.id))) or 0
    active_enrollments = db.session.scalar(
        db.select(func.count(StudentEnrollment.id)).where(
            StudentEnrollment.academic_year == _current_term(),
            StudentEnrollment.enrollment_status == "enrolled",
        )
    ) or 0
    curriculum_pending = db.session.scalar(
        db.select(func.count(CurriculumVersion.id)).where(CurriculumVersion.status.in_(["draft", "review"]))
    ) or 0
    import_pending = db.session.scalar(
        db.select(func.count(ResultImport.id)).where(ResultImport.status.in_(["uploaded", "processing", "review_required", "ready_to_link", "partially_linked"]))
    ) or 0
    pending_rows = db.session.scalar(
        db.select(func.count(StagedResult.id)).where(StagedResult.review_status == "pending")
    ) or 0
    latest_enrollments = db.session.execute(
        db.select(StudentEnrollment, Student)
        .join(Student, Student.id == StudentEnrollment.student_id)
        .order_by(StudentEnrollment.created_at.desc())
        .limit(6)
    ).all()
    # Semester distribution is derived from stored enrollment records, never hard-coded.
    semester_rows = db.session.execute(
        db.select(StudentEnrollment.semester_number, func.count(StudentEnrollment.id))
        .where(StudentEnrollment.academic_year == _current_term())
        .group_by(StudentEnrollment.semester_number)
        .order_by(StudentEnrollment.semester_number)
    ).all()
    activity = db.session.execute(
        db.select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(6)
    ).scalars().all()
    return render_template(
        "dashboard.html",
        page_title="Overview",
        student_count=student_count,
        active_enrollments=active_enrollments,
        curriculum_pending=curriculum_pending,
        import_pending=import_pending,
        pending_rows=pending_rows,
        latest_enrollments=latest_enrollments,
        semester_labels=[f"Sem {r[0]}" for r in semester_rows],
        semester_values=[r[1] for r in semester_rows],
        activity=activity,
        academic_year=_current_term(),
    )


@bp.get("/curriculum")
@login_required
def curriculum():
    versions = db.session.execute(
        db.select(CurriculumVersion, func.count(CurriculumSubject.id).label("subject_count"))
        .outerjoin(CurriculumSubject, CurriculumSubject.curriculum_version_id == CurriculumVersion.id)
        .group_by(CurriculumVersion.id)
        .order_by(CurriculumVersion.created_at.desc())
    ).all()
    programs = db.session.execute(
        db.select(AcademicProgram).where(AcademicProgram.is_active.is_(True)).order_by(AcademicProgram.name.asc())
    ).scalars().all()
    schemes = db.session.execute(
        db.select(CurriculumScheme).where(CurriculumScheme.is_active.is_(True)).order_by(CurriculumScheme.display_name.asc())
    ).scalars().all()
    return render_template(
        "curriculum.html", page_title="Academic Setup", versions=versions,
        programs=programs, schemes=schemes, current_year=datetime.now().year,
    )


@bp.get("/result-imports")
@login_required
def result_imports():
    """Compatibility route forwarding to the fully implemented staged import workspace."""
    return redirect(url_for("results.index"))


@bp.get("/analytics")
@login_required
def analytics():
    policy_pending = db.session.scalar(
        db.select(func.count(AcademicPolicy.id)).where(AcademicPolicy.status.in_(["draft", "review"]))
    ) or 0
    active_policy_count = db.session.scalar(
        db.select(func.count(AcademicPolicy.id)).where(AcademicPolicy.status == "active")
    ) or 0
    assessment_count = db.session.scalar(db.select(func.count(Assessment.id))) or 0
    mark_count = db.session.scalar(db.select(func.count(AssessmentMark.id))) or 0
    attendance_sessions = db.session.scalar(db.select(func.count(AttendanceSession.id))) or 0
    attendance_records = db.session.scalar(db.select(func.count(AttendanceRecord.id))) or 0
    verified_attempts = db.session.scalar(db.select(func.count(ExamAttempt.id)).where(ExamAttempt.verification_status == "verified")) or 0
    historical_failures = db.session.scalar(db.select(func.count(ExamAttempt.id)).where(
        ExamAttempt.verification_status == "verified", ExamAttempt.official_status == "fail")) or 0
    # This is a descriptive aggregate, not official attendance eligibility.
    present_count = db.session.scalar(db.select(func.count(AttendanceRecord.id)).where(AttendanceRecord.status == "present")) or 0
    absent_count = db.session.scalar(db.select(func.count(AttendanceRecord.id)).where(AttendanceRecord.status == "absent")) or 0
    attendance_denominator = present_count + absent_count
    attendance_percent = round(100 * present_count / attendance_denominator, 2) if attendance_denominator else None
    subject_stats = db.session.execute(
        db.select(CurriculumSubject.subject_code, CurriculumSubject.title,
                  func.count(AssessmentMark.id).label("mark_count"),
                  func.avg(AssessmentMark.marks_obtained / Assessment.max_marks * 100).label("average_percent"))
        .join(Assessment, Assessment.curriculum_subject_id == CurriculumSubject.id)
        .join(AssessmentMark, AssessmentMark.assessment_id == Assessment.id)
        .where(AssessmentMark.status == "present", AssessmentMark.marks_obtained.is_not(None))
        .group_by(CurriculumSubject.id, CurriculumSubject.subject_code, CurriculumSubject.title)
        .order_by(CurriculumSubject.subject_code).limit(30)
    ).all()
    return render_template(
        "analytics.html", page_title="Analytics & Reports",
        policy_pending=policy_pending, active_policy_count=active_policy_count,
        assessment_count=assessment_count, mark_count=mark_count,
        attendance_sessions=attendance_sessions, attendance_records=attendance_records,
        attendance_percent=attendance_percent, verified_attempts=verified_attempts,
        historical_failures=historical_failures, subject_stats=subject_stats,
    )


@bp.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db.session.rollback()
        db_status = "error"
    status_code = 200 if db_status == "ok" else 503
    return jsonify({"status": "ok" if db_status == "ok" else "degraded", "database": db_status}), status_code


@bp.errorhandler(403)
def forbidden(_error):
    return render_template("errors/403.html", page_title="Access denied"), 403
