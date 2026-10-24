"""Policy-gated semester and cumulative calculation workflow.

Math delegates to the old repository's pure SGPA/CGPA and grade helpers. This
service decides whether the available, reviewed attempts cover the required
subjects; it does not infer which attempt wins.
"""
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from sqlalchemy import select, or_

from ..extensions import db
from ..models import AcademicPolicy, CurriculumSubject, ExamAttempt, StudentElectiveSelection, StudentEnrollment
from .legacy_calculations import GRADE_TABLE, calculate_cgpa, calculate_sgpa, get_grade, is_gradable_subject
from .legacy_mark_service import LEGACY_PROFILE_ID


def active_grading_policy(enrollment):
    policies = db.session.execute(select(AcademicPolicy).where(
        AcademicPolicy.policy_type == "grading", AcademicPolicy.status == "active",
        or_(AcademicPolicy.program_id == enrollment.program_id, AcademicPolicy.program_id.is_(None)),
        or_(AcademicPolicy.curriculum_version_id == enrollment.curriculum_version_id, AcademicPolicy.curriculum_version_id.is_(None)),
        or_(AcademicPolicy.effective_from_cohort_year == enrollment.curriculum_cohort_year,
            AcademicPolicy.effective_from_cohort_year.is_(None)),
    )).scalars().all()
    compatible = [p for p in policies if (p.rules_json or {}).get("calculation_profile") == LEGACY_PROFILE_ID]
    compatible.sort(key=lambda p: (p.curriculum_version_id is not None, p.program_id is not None,
                                   p.effective_from_cohort_year is not None, p.created_at), reverse=True)
    return compatible[0] if compatible else None


def _subject_is_gradable(subject, elective_approved_subject_ids):
    course_type = (subject.course_type or "").lower()
    if course_type in {"audit", "attendance_only", "elective_group", "container"}:
        return False
    if course_type == "elective" and subject.id not in elective_approved_subject_ids:
        return False
    proxy = SimpleNamespace(
        credits=subject.credits,
        is_audit=course_type == "audit",
        is_attendance_only=course_type == "attendance_only",
        is_active=subject.review_status != "rejected",
        is_elective=False,  # Actual option rows, not elective-group containers.
        parent_subject_code=None,
    )
    return is_gradable_subject(proxy)


def _effective_attempt_for(enrollment, subject):
    attempts = db.session.execute(select(ExamAttempt).where(
        ExamAttempt.student_id == enrollment.student_id,
        ExamAttempt.curriculum_subject_id == subject.id,
        ExamAttempt.semester_number == enrollment.semester_number,
        ExamAttempt.academic_year == enrollment.academic_year,
        ExamAttempt.enrollment_id == enrollment.id,
        ExamAttempt.verification_status == "verified",
        ExamAttempt.effective_for_calculation.is_(True),
    ).order_by(ExamAttempt.verified_at.desc(), ExamAttempt.id.desc())).scalars().all()
    if len(attempts) > 1:
        return None, "Multiple attempts are marked effective. Resolve the conflict before calculation."
    return (attempts[0], None) if attempts else (None, "No verified attempt is selected as effective under the applicable policy.")


def compute_enrollment_sgpa(enrollment):
    """Return a calculation preview and the policy/coverage needed to judge it."""
    version = enrollment.curriculum_version
    policy = active_grading_policy(enrollment)
    if not version:
        return {"sgpa": None, "credits_registered": 0, "credits_earned": 0, "subjects": [],
                "policy": policy, "complete": False, "required_subject_count": 0,
                "covered_subject_count": 0, "status": "policy_pending",
                "message": "This enrollment has no curriculum version assigned."}
    approved_electives = set(db.session.execute(select(StudentElectiveSelection.curriculum_subject_id).where(
        StudentElectiveSelection.enrollment_id == enrollment.id,
        StudentElectiveSelection.selection_status == "approved")).scalars().all())
    candidates = db.session.execute(select(CurriculumSubject).where(
        CurriculumSubject.curriculum_version_id == version.id,
        CurriculumSubject.semester_number == enrollment.semester_number,
        CurriculumSubject.review_status == "verified").order_by(CurriculumSubject.subject_code)).scalars().all()
    subjects = [s for s in candidates if _subject_is_gradable(s, approved_electives)]
    results = []
    detail_rows = []
    warnings = []
    covered = 0
    for subject in subjects:
        attempt, issue = _effective_attempt_for(enrollment, subject)
        credits = None
        try:
            credits = int(Decimal(subject.credits or 0))
            if Decimal(subject.credits or 0) != Decimal(credits):
                raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            issue = "Subject credits are not an integer supported by the legacy calculator."
        grade_point = None
        is_passed = None
        grade_source = None
        if attempt and not issue:
            if attempt.official_status not in {"pass", "fail", "absent"}:
                issue = f"Attempt status {attempt.official_status} is not a completed pass/fail outcome."
            elif attempt.official_status == "fail" or attempt.official_status == "absent":
                grade_point = Decimal("0")
                is_passed = False
                grade_source = "official status"
            elif attempt.official_grade_point is not None:
                candidate_grade_point = Decimal(attempt.official_grade_point)
                if not Decimal("0") <= candidate_grade_point <= Decimal("10"):
                    issue = "Reported grade point is outside the permitted 0–10 range."
                else:
                    grade_point = candidate_grade_point
                    is_passed = True
                    grade_source = "reported grade point"
            elif attempt.official_grade:
                # Derive grade-point mapping directly from the preserved legacy table,
                # avoiding a duplicated/independently drifting grade map.
                grade_map = {row[2]: row[3] for row in GRADE_TABLE}
                gp = grade_map.get(attempt.official_grade.upper())
                if gp is None:
                    issue = "Official grade is not mapped in the active legacy grade table."
                else:
                    grade_point = gp
                    is_passed = True
                    grade_source = "reported grade mapped under legacy profile"
            elif attempt.total_marks is not None:
                grade, gp = get_grade(float(attempt.total_marks))
                grade_point = gp
                is_passed = True
                grade_source = f"provisional grade derived from total ({grade})"
            else:
                issue = "No reported grade point, grade or total marks are available."
        if attempt and not issue:
            covered += 1
            results.append({"credits_registered": credits or 0, "credits_earned": credits if is_passed else 0,
                            "grade_point": grade_point, "is_passed": is_passed})
        else:
            warnings.append(f"{subject.subject_code}: {issue or 'incomplete'}")
        detail_rows.append({"subject": subject, "attempt": attempt, "issue": issue, "credits": credits,
                            "grade_point": grade_point, "is_passed": is_passed, "grade_source": grade_source})
    sgpa, credits_registered, credits_earned = calculate_sgpa(results)
    complete = bool(subjects) and covered == len(subjects)
    status = "calculated" if policy and complete and sgpa is not None else ("policy_pending" if not policy else "provisional")
    if not subjects:
        warnings.append("No gradable verified subjects are configured for this semester.")
    if not policy:
        warnings.append("No active policy is mapped to the legacy calculation profile; numeric output is a preview only.")
    if not complete:
        warnings.append("Not all gradable subjects have exactly one verified effective attempt.")
    return {"sgpa": sgpa, "credits_registered": credits_registered, "credits_earned": credits_earned,
            "subjects": detail_rows, "policy": policy, "complete": complete,
            "required_subject_count": len(subjects), "covered_subject_count": covered,
            "status": status, "warnings": warnings}


def compute_student_cgpa(student_id):
    """Calculate CGPA only from approved SGPA snapshots for all completed enrollments.

    If any recorded enrolled/completed semester lacks an approved SGPA snapshot,
    return pending rather than silently using a partial average.
    """
    from ..models import CalculationSnapshot, StudentEnrollment
    enrollments = db.session.execute(select(StudentEnrollment).where(
        StudentEnrollment.student_id == student_id,
        StudentEnrollment.enrollment_status.in_(("enrolled", "detained", "completed")),
    ).order_by(StudentEnrollment.academic_year, StudentEnrollment.semester_number)).scalars().all()
    approved_snapshots = db.session.execute(select(CalculationSnapshot).where(
        CalculationSnapshot.student_id == student_id, CalculationSnapshot.calculation_type == "sgpa",
        CalculationSnapshot.status == "approved").order_by(CalculationSnapshot.academic_year, CalculationSnapshot.semester_number)).scalars().all()
    by_enrollment = {}
    for snap in approved_snapshots:
        if snap.enrollment_id:
            by_enrollment[snap.enrollment_id] = snap
    values = []
    missing = []
    used_snapshots = []
    for enrollment in enrollments:
        snap = by_enrollment.get(enrollment.id)
        if not snap or snap.value_numeric is None or not (snap.details_json or {}).get("credits_registered"):
            missing.append(enrollment)
            values.append({"sgpa": None, "credits_registered": 0, "credits_earned": 0})
        else:
            used_snapshots.append(snap)
            values.append({"sgpa": snap.value_numeric,
                           "credits_registered": (snap.details_json or {}).get("credits_registered", 0),
                           "credits_earned": (snap.details_json or {}).get("credits_earned", 0)})
    cgpa = calculate_cgpa(values) if values and not missing else None
    return {"cgpa": cgpa, "enrollments": enrollments, "snapshots": by_enrollment,
            "used_snapshots": used_snapshots, "missing": missing}


def evaluate_promotion(enrollment):
    """Evaluate only a complete explicit policy schema; never supply defaults.

    Supported rule schema:
    {
      "rule_type": "credit_and_backlog_threshold",
      "required_semesters": [1, 2],
      "minimum_credit_percentage": 75,
      "maximum_active_backlogs": 0
    }
    These values are examples of schema shape only. The institution must supply
    the approved values and source reference; no threshold is preconfigured.
    """
    policies = db.session.execute(select(AcademicPolicy).where(
        AcademicPolicy.policy_type == "promotion", AcademicPolicy.status == "active",
        or_(AcademicPolicy.program_id == enrollment.program_id, AcademicPolicy.program_id.is_(None)),
        or_(AcademicPolicy.curriculum_version_id == enrollment.curriculum_version_id, AcademicPolicy.curriculum_version_id.is_(None)),
        or_(AcademicPolicy.effective_from_cohort_year == enrollment.curriculum_cohort_year, AcademicPolicy.effective_from_cohort_year.is_(None)),
    )).scalars().all()
    policies.sort(key=lambda p: (p.curriculum_version_id is not None, p.program_id is not None, p.effective_from_cohort_year is not None), reverse=True)
    policy = policies[0] if policies else None
    if not policy:
        return {"status": "policy_pending", "message": "No active promotion policy applies to this programme/cohort."}
    rules = policy.rules_json or {}
    try:
        semesters = [int(x) for x in rules["required_semesters"]]
        minimum_credit_percentage = Decimal(str(rules["minimum_credit_percentage"]))
        maximum_backlogs = int(rules["maximum_active_backlogs"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return {"status": "policy_pending", "policy": policy, "message": "Active promotion policy does not use the supported explicit credit/backlog rule schema."}
    if rules.get("rule_type") != "credit_and_backlog_threshold" or not semesters or any(not 1 <= s <= 12 for s in semesters) or not Decimal(0) <= minimum_credit_percentage <= Decimal(100) or maximum_backlogs < 0:
        return {"status": "policy_pending", "policy": policy, "message": "Promotion policy values are invalid or incomplete."}
    student_id = enrollment.student_id
    total_credits = 0
    earned_credits = 0
    active_backlogs = 0
    missing = []
    for semester in semesters:
        term_enrollment = db.session.scalar(select(StudentEnrollment).where(
            StudentEnrollment.student_id == student_id, StudentEnrollment.semester_number == semester,
            StudentEnrollment.program_id == enrollment.program_id,
            StudentEnrollment.enrollment_status.in_(("enrolled", "detained", "completed")),
        ).order_by(StudentEnrollment.academic_year.desc()))
        if not term_enrollment or not term_enrollment.curriculum_version_id:
            missing.append(f"Semester {semester}: enrollment/curriculum missing")
            continue
        version_subjects = db.session.execute(select(CurriculumSubject).where(
            CurriculumSubject.curriculum_version_id == term_enrollment.curriculum_version_id,
            CurriculumSubject.semester_number == semester, CurriculumSubject.review_status == "verified")).scalars().all()
        approved_electives = set(db.session.execute(select(StudentElectiveSelection.curriculum_subject_id).where(
            StudentElectiveSelection.enrollment_id == term_enrollment.id, StudentElectiveSelection.selection_status == "approved")).scalars().all())
        for subject in version_subjects:
            if not _subject_is_gradable(subject, approved_electives):
                continue
            try:
                credit_value = Decimal(subject.credits or 0)
                if credit_value != credit_value.to_integral_value():
                    raise ValueError
                credits = int(credit_value)
            except (InvalidOperation, ValueError):
                missing.append(f"{subject.subject_code}: unsupported credits")
                continue
            total_credits += credits
            attempt, issue = _effective_attempt_for(term_enrollment, subject)
            if issue or not attempt or attempt.official_status not in {"pass", "fail", "absent"}:
                missing.append(f"{subject.subject_code}: {issue or 'result not final'}")
            elif attempt.official_status == "pass":
                earned_credits += credits
            else:
                active_backlogs += 1
    if missing or total_credits == 0:
        return {"status": "policy_pending", "policy": policy, "message": "Promotion cannot be evaluated until all required semester results are complete.",
                "missing": missing, "total_credits": total_credits, "earned_credits": earned_credits, "active_backlogs": active_backlogs}
    credit_percent = Decimal(earned_credits * 100) / Decimal(total_credits)
    eligible = credit_percent >= minimum_credit_percentage and active_backlogs <= maximum_backlogs
    return {"status": "eligible" if eligible else "not_eligible", "policy": policy, "credit_percentage": credit_percent,
            "total_credits": total_credits, "earned_credits": earned_credits, "active_backlogs": active_backlogs,
            "rule_summary": rules}
