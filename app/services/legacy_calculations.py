"""Legacy DBATU/ECE calculation profile carried from the previous repository.

Source reference: Student-analysis-system-/calculations.py (owner-provided reference).
The mathematical behavior below is preserved for compatibility. It is not a
claim that these rules apply to every scheme or that the institution has approved
them for all cohorts. Route official calculations through an active, versioned
AcademicPolicy and record the profile/version in calculation snapshots.

The ORM updater in this legacy module expects the old repository's model fields.
It is retained as migration reference only; AcadPulse must use an adapter for its
new schema rather than calling that old ORM updater directly.
"""

# ─────────────────────────────────────────────────────────────────────────────
#  calculations.py
#  ECE Student Management System  |  CSMSS Chh. Shahu College of Engineering
#
#  PURPOSE:
#    Helper functions for final result computation once External marks arrive.
#    All functions are PURE (no side-effects) — test them independently first.
#    Call these from your routes AFTER saving ExternalMarks to DB.
#
#  INTEGRATION:
#    Import into your existing calculations.py   OR   import directly in routes:
#      from calculations import compute_final_result, update_sgpa_cgpa
# ─────────────────────────────────────────────────────────────────────────────

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


# ── GRADING TABLE (as per DBATU Syllabus) ────────────────────────────────────

GRADE_TABLE = [
    (91, 100, "EX", Decimal("10.0")),
    (86,  90, "AA", Decimal("9.0")),
    (81,  85, "AB", Decimal("8.5")),
    (76,  80, "BB", Decimal("8.0")),
    (71,  75, "BC", Decimal("7.5")),
    (66,  70, "CC", Decimal("7.0")),
    (61,  65, "CD", Decimal("6.5")),
    (56,  60, "DD", Decimal("6.0")),
    (51,  55, "DE", Decimal("5.5")),
    (40,  50, "EE", Decimal("5.0")),
    ( 0,  39, "FF", Decimal("0.0")),   # FAIL
]


def get_grade(total_marks: float):
    """
    Returns (grade_letter, grade_point) for a given total out of 100.
    Always based on TOTAL marks — NOT on external alone.
    """
    total = float(total_marks)
    for low, high, grade, gp in GRADE_TABLE:
        if low <= total <= high:
            return grade, gp
    return "FF", Decimal("0.0")


# ── SUBJECT / ENROLLMENT FILTERS ─────────────────────────────────────────────

def is_gradable_subject(subject):
    """Return True only for subjects that should affect result / SGPA / CGPA.

    Important for electives:
    - Parent rows like BTECHM505 / BTECPE503 / BTECOE504 are only containers.
      They should NEVER be counted in SGPA/CGPA and should never make a result
      pending. Only option rows whose parent_subject_code is filled are counted.
    - Audit / attendance-only / inactive subjects are also ignored.
    """
    if subject is None:
        return False

    if bool(getattr(subject, "is_audit", False)):
        return False

    if bool(getattr(subject, "is_attendance_only", False)):
        return False

    if getattr(subject, "is_active", True) is False:
        return False

    try:
        if int(getattr(subject, "credits", 0) or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False

    # Elective parent rows are placeholders/containers, not actual subjects.
    if bool(getattr(subject, "is_elective", False)) and not getattr(subject, "parent_subject_code", None):
        return False

    return True


# ── FINAL RESULT COMPUTATION ──────────────────────────────────────────────────

def compute_final_result(internal_marks: float, external_marks: float, credits: int):
    """
    Combines Internal (max 40) + External (max 60) → Final result dict.

    Pass conditions (BOTH must hold):
      1. Total >= 40
      2. External >= 20  (ESE minimum)

    Returns dict with:
      internal, external, total, grade, grade_point,
      credits_registered, credits_earned, is_passed, fail_reason
    """
    internal = float(internal_marks) if internal_marks is not None else None
    external = float(external_marks) if external_marks is not None else None

    result = {
        "internal":           round(internal, 1) if internal is not None else None,
        "external":           round(external, 1) if external is not None else None,
        "total":              None,
        "grade":              None,
        "grade_point":        None,
        "credits_registered": credits,
        "credits_earned":     0,
        "is_passed":          None,
        "fail_reason":        None,
    }

    if internal is None:
        result["fail_reason"] = "Internal marks not complete"
        return result

    if external is None:
        result["fail_reason"] = "External marks not entered"
        return result

    total = internal + external
    result["total"] = round(total, 1)

    # Determine pass / fail
    ese_pass   = external >= 20.0
    total_pass = total   >= 40.0

    if not ese_pass:
        result["is_passed"]   = False
        result["fail_reason"] = f"ESE below minimum (got {external}/60, need ≥20)"
        result["grade"]       = "FF"
        result["grade_point"] = Decimal("0.0")
        result["credits_earned"] = 0
    elif not total_pass:
        result["is_passed"]   = False
        result["fail_reason"] = f"Total below 40 (got {total}/100)"
        result["grade"]       = "FF"
        result["grade_point"] = Decimal("0.0")
        result["credits_earned"] = 0
    else:
        grade, gp = get_grade(total)
        result["is_passed"]      = True
        result["grade"]          = grade
        result["grade_point"]    = gp
        result["credits_earned"] = credits
        result["fail_reason"]    = None

    return result


def compute_lab_result(ca1_marks: float, ca2_marks: float, external_marks: float, credits: int):
    """
    Lab / Project result formula.

    CA1      = /30
    CA2      = /30
    Internal = CA1 + CA2 = /60
    External = /40
    Total    = /100

    Pass condition:
      Total >= 40

    Returns dict with internal, external, total, grade, grade_point,
    credits_registered, credits_earned, is_passed, fail_reason.
    """
    ca1 = float(ca1_marks) if ca1_marks is not None else None
    ca2 = float(ca2_marks) if ca2_marks is not None else None
    external = float(external_marks) if external_marks is not None else None

    result = {
        "ca1": ca1,
        "ca2": ca2,
        "internal": None,
        "external": round(external, 1) if external is not None else None,
        "total": None,
        "grade": None,
        "grade_point": None,
        "credits_registered": credits,
        "credits_earned": 0,
        "is_passed": None,
        "fail_reason": None,
    }

    if ca1 is None or ca2 is None:
        result["fail_reason"] = "CA1/CA2 marks not entered"
        return result

    if external is None:
        result["fail_reason"] = "External lab marks not entered"
        return result

    internal = ca1 + ca2
    total = internal + external

    result["internal"] = round(internal, 1)
    result["total"] = round(total, 1)

    if total < 40.0:
        result["grade"] = "FF"
        result["grade_point"] = Decimal("0.0")
        result["is_passed"] = False
        result["credits_earned"] = 0
        result["fail_reason"] = f"Total below 40 (got {total}/100)"
    else:
        grade, gp = get_grade(total)
        result["grade"] = grade
        result["grade_point"] = gp
        result["is_passed"] = True
        result["credits_earned"] = credits
        result["fail_reason"] = None

    return result


# ── SGPA CALCULATION ──────────────────────────────────────────────────────────

def calculate_sgpa(subject_results: list):
    """
    Safe SGPA calculation.

    Rules:
    - Completed passed/failed subjects are included.
    - Pending/incomplete subjects are skipped.
    - Failed subjects count in registered credits with grade point 0.
    - None grade_point / None is_passed will NOT crash the app.

    Returns: (sgpa, credits_registered_total, credits_earned_total)
    """
    total_credit_points = Decimal("0.0")
    total_credits_registered = 0
    total_credits_earned = 0

    for r in subject_results or []:
        credits = r.get("credits_registered", r.get("credits", 0))
        grade_point = r.get("grade_point")
        is_passed = r.get("is_passed")

        try:
            cr = int(credits or 0)
        except (TypeError, ValueError):
            continue

        if cr <= 0:
            continue

        # Pending/incomplete result: skip from SGPA instead of crashing or inflating.
        if grade_point is None or is_passed is None:
            continue

        try:
            gp = Decimal(str(grade_point))
        except (InvalidOperation, TypeError, ValueError):
            continue

        total_credit_points += cr * gp
        total_credits_registered += cr

        if bool(is_passed):
            total_credits_earned += cr

    if total_credits_registered == 0:
        # No completed non-audit subjects yet. Keep SGPA pending instead
        # of writing a misleading 0.00 value.
        return None, 0, 0

    sgpa = (total_credit_points / total_credits_registered).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return sgpa, total_credits_registered, total_credits_earned


def calculate_cgpa(semester_sgpa_list: list):
    """
    semester_sgpa_list: list of dicts, each with:
        credits_registered, credits_earned, sgpa
        (one dict per completed semester)

    Returns: cgpa (Decimal) or None when CGPA is still pending.
    """
    numerator = Decimal("0.0")
    denominator = 0

    for s in semester_sgpa_list or []:
        sgpa = s.get("sgpa")
        if sgpa is None:
            return None

        try:
            cr = int(s.get("credits_registered") or 0)
            sg = Decimal(str(sgpa))
        except (TypeError, ValueError, InvalidOperation):
            return None

        if cr <= 0:
            return None

        numerator += cr * sg
        denominator += cr

    if denominator == 0:
        return None

    return (numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── DB UPDATE HELPERS (call from Flask routes after saving ExternalMarks) ─────

def update_sgpa_cgpa_for_student(prn, semester_id, academic_year, db, models, commit=True):
    """
    Recalculates and saves SGPA + CGPA for one student after external marks upload.

    Parameters:
        prn           : str
        semester_id   : int
        academic_year : str  e.g. "2024-25"
        db            : SQLAlchemy db instance
        models        : module or object exposing:
                          TheoryMarks, ExternalMarks, Subject,
                          Enrollment, SgpaCgpa, LabMarks

    Returns: dict  { "sgpa": ..., "cgpa": ..., "credits_registered": ..., "credits_earned": ... }
    """
    # 1. Fetch all enrolled subjects for this semester (non-audit)
    enrollments = (
        models.Enrollment.query
        .filter_by(prn=prn, semester_id=semester_id, academic_year=academic_year)
        .all()
    )

    subject_results = []
    has_missing_marks = False
    total_non_audit_credits = 0

    for enr in enrollments:
        subj = models.Subject.query.filter_by(subject_code=enr.subject_code).first()
        if not is_gradable_subject(subj):
            continue  # skip audit / inactive / attendance-only / elective parent rows

        total_non_audit_credits += int(subj.credits or 0)

        if subj.subject_type == 'THEORY':
            tm_row = models.TheoryMarks.query.filter_by(
                prn=prn,
                subject_code=subj.subject_code,
                semester_id=semester_id,
                academic_year=academic_year,
            ).first()

            ext_row = models.ExternalMarks.query.filter_by(
                prn=prn,
                subject_code=subj.subject_code,
                semester_id=semester_id,
                academic_year=academic_year,
            ).first()

            if tm_row is not None:
                # Refresh internal_total using the current internal formula before SGPA/CGPA.
                update_internal_totals(tm_row)

            ext_val = None
            if tm_row and tm_row.external is not None:
                ext_val = float(tm_row.external)
            elif ext_row and ext_row.external_marks is not None:
                ext_val = float(ext_row.external_marks)

            if (
                tm_row is None
                or not is_theory_internal_complete(tm_row)
                or tm_row.internal_total is None
                or ext_val is None
            ):
                has_missing_marks = True
            else:
                internal_val = float(tm_row.internal_total)
                res = compute_final_result(internal_val, ext_val, subj.credits)
                if res["is_passed"] is None:
                    has_missing_marks = True
                else:
                    # Keep the marks row itself in sync when recalculating SGPA/CGPA.
                    # This is important after formula changes and one-time refresh scripts.
                    tm_row.external = round(ext_val, 2)
                    tm_row.total_marks = res["total"]
                    tm_row.grade = res["grade"]
                    tm_row.grade_point = res["grade_point"]
                    tm_row.is_passed = res["is_passed"]
                    subject_results.append(res)
        else:
            # LAB, PROJECT, etc.
            # Formula: CA1 /30 + CA2 /30 = Internal /60, External /40, Total /100
            lm_row = models.LabMarks.query.filter_by(
                prn=prn,
                subject_code=subj.subject_code,
                semester_id=semester_id,
                academic_year=academic_year,
            ).first()

            if lm_row is None or lm_row.external is None:
                has_missing_marks = True
            else:
                ca1 = getattr(lm_row, "ca1", None)
                ca2 = getattr(lm_row, "ca2", None)

                # New preferred formula: ca1 + ca2.
                # Compatibility: if an older row has only internal/external, keep using internal.
                if ca1 is not None and ca2 is not None:
                    lab_res = compute_lab_result(ca1, ca2, lm_row.external, subj.credits)
                    lm_row.internal = lab_res["internal"]
                    lm_row.total_marks = lab_res["total"]
                    lm_row.grade = lab_res["grade"]
                    lm_row.grade_point = lab_res["grade_point"]
                    lm_row.is_passed = lab_res["is_passed"]
                    subject_results.append(lab_res)
                elif lm_row.internal is not None:
                    total = float(lm_row.internal) + float(lm_row.external)
                    if lm_row.grade_point is None or lm_row.total_marks is None:
                        grade, gp = get_grade(total) if total >= 40 else ("FF", Decimal("0.0"))
                        lm_row.total_marks = round(total, 2)
                        lm_row.grade = grade
                        lm_row.grade_point = gp
                        lm_row.is_passed = total >= 40
                    subject_results.append({
                        "credits_registered": subj.credits,
                        "credits_earned": subj.credits if lm_row.is_passed else 0,
                        "grade_point": float(lm_row.grade_point) if lm_row.grade_point is not None else 0.0,
                        "is_passed": lm_row.is_passed
                    })
                else:
                    has_missing_marks = True

    # 2. Upsert into sgpa_cgpa table for THIS semester
    row = models.SgpaCgpa.query.filter_by(
        prn=prn,
        semester_id=semester_id,
        academic_year=academic_year,
    ).first()

    if row is None:
        row = models.SgpaCgpa(
            prn=prn,
            semester_id=semester_id,
            academic_year=academic_year,
        )
        db.session.add(row)

    if has_missing_marks or total_non_audit_credits <= 0:
        sgpa = None
        cr_reg = total_non_audit_credits
        cr_earned = 0

        row.sgpa               = None
        row.credits_registered = cr_reg
        row.credits_earned     = cr_earned
    else:
        sgpa, cr_reg, cr_earned = calculate_sgpa(subject_results)
        row.sgpa               = sgpa
        row.credits_registered = cr_reg
        row.credits_earned     = cr_earned

    # 3. Recalculate CGPA correctly for each semester row.
    #
    # IMPORTANT:
    # Old logic calculated one latest CGPA and copied it to every semester row.
    # Correct logic stores historical cumulative CGPA:
    #   Sem 3 row -> CGPA up to Sem 3 only
    #   Sem 4 row -> CGPA up to Sem 4 only
    #   Sem 5 row -> CGPA up to Sem 5 only
    #   Sem 6 row -> CGPA up to Sem 6 only
    all_sem_rows = (
        models.SgpaCgpa.query
        .filter_by(prn=prn)
        .order_by(models.SgpaCgpa.semester_id)
        .all()
    )

    running_credit_points = Decimal("0.0")
    running_registered_credits = 0
    cgpa_for_requested_semester = None
    cgpa_blocked_by_pending_semester = False

    for s in all_sem_rows:
        # Once any earlier semester is pending, CGPA for that semester and every
        # later semester must stay pending. Otherwise Sem 6 could wrongly show
        # CGPA using only Sem 6 while Sem 3/4/5 is incomplete.
        if s.sgpa is None or cgpa_blocked_by_pending_semester:
            s.cgpa = None
            cgpa_blocked_by_pending_semester = True
            if s.semester_id == semester_id and s.academic_year == academic_year:
                cgpa_for_requested_semester = None
            continue

        cr = int(s.credits_registered or 0)
        if cr <= 0:
            s.cgpa = None
            cgpa_blocked_by_pending_semester = True
            if s.semester_id == semester_id and s.academic_year == academic_year:
                cgpa_for_requested_semester = None
            continue

        sg = Decimal(str(s.sgpa))
        running_credit_points += cr * sg
        running_registered_credits += cr

        current_cgpa = (running_credit_points / running_registered_credits).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        s.cgpa = current_cgpa

        if s.semester_id == semester_id and s.academic_year == academic_year:
            cgpa_for_requested_semester = current_cgpa

    if commit:
        db.session.commit()

    return {
        "sgpa":               float(sgpa) if sgpa is not None else None,
        "cgpa":               float(cgpa_for_requested_semester) if cgpa_for_requested_semester is not None else None,
        "credits_registered": cr_reg,
        "credits_earned":     cr_earned,
    }


# ── VALIDATION ────────────────────────────────────────────────────────────────

def validate_external_marks(value, subject_type="THEORY"):
    """
    Returns (is_valid: bool, error_message: str | None)
    Theory external: 0 – 60
    Lab external   : 0 – 40  (if you ever need it)
    """
    if value is None or str(value).strip() == "":
        return False, "External marks cannot be empty"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return False, f"'{value}' is not a valid number"

    if subject_type.upper() == "THEORY":
        if not (0.0 <= v <= 60.0):
            return False, f"Theory external must be 0–60 (got {v})"
    else:
        if not (0.0 <= v <= 40.0):
            return False, f"Lab external must be 0–40 (got {v})"

    return True, None



def validate_lab_component(value, max_marks, label):
    """Validate one lab component mark."""
    if value is None or str(value).strip() == "":
        return False, f"{label} cannot be empty"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return False, f"{label} must be a valid number"
    if not (0.0 <= v <= float(max_marks)):
        return False, f"{label} must be 0-{max_marks} (got {v})"
    return True, None


def is_theory_internal_complete(row):
    """Return True only when all theory internal components are present.

    This keeps final result/SGPA as PENDING when CT1/CT2/Assignment/Midsem
    are not fully uploaded yet, instead of treating missing components as 0.
    """
    return all(
        getattr(row, field, None) is not None
        for field in ("ct1", "ct2", "assignment", "midsem")
    )


def update_internal_totals(row):
    """
    Internal formula for THEORY subjects.

    CT1        = /10
    CT2        = /10
    Assignment = /10
    MSE/Midsem = /20

    CA1 = highest mark from CT1, CT2, Assignment
    CA2 = highest mark from the remaining two components
    CA  = CA1 + CA2                 (/20)
    Internal Total = CA + MSE       (/40)

    Existing DB columns are reused without changing the database schema:
      best_ct        -> CA1 / highest component
      ca_marks       -> CA1 + CA2
      internal_total -> ca_marks + MSE
    """
    if row is None:
        return row

    # Do not treat missing internal components as zero. Missing CT/Assignment/MSE
    # means the subject result is still PENDING, not FAIL and not 0 internal.
    if not is_theory_internal_complete(row):
        row.best_ct = None
        row.ca_marks = None
        row.internal_total = None
        return row

    ct1 = float(row.ct1)
    ct2 = float(row.ct2)
    assignment = float(row.assignment)
    midsem = float(row.midsem)

    ca1, ca2 = sorted([ct1, ct2, assignment], reverse=True)[:2]
    ca_marks = ca1 + ca2

    row.best_ct = round(ca1, 2)
    row.ca_marks = round(ca_marks, 2)
    row.internal_total = round(ca_marks + midsem, 2)

    return row

