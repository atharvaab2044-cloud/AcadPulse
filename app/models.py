"""Core AcadPulse data model.

Design invariants:
- Student.id is an internal, stable identity; PRN and roll number are not PKs.
- Roll number, division, and practical batch live on term-specific enrollment.
- Curriculum versions and examination attempts are historical records, not mutable snapshots.
- PDF extraction is staged separately from linked official exam attempts.
- Academic policies must be approved before authoritative calculations may use them.
"""
from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, Index
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="faculty", index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (CheckConstraint("role IN ('admin', 'faculty', 'viewer')", name="valid_user_role"),)

    def set_password(self, password: str) -> None:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters.")
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def can_manage_academics(self) -> bool:
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class Student(TimestampMixin, db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)  # Internal permanent identity.
    student_code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(180), nullable=False, index=True)
    email = db.Column(db.String(254), nullable=True, index=True)
    phone = db.Column(db.String(32), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(24), nullable=False, default="active", index=True)
    notes = db.Column(db.Text, nullable=True)

    identifiers = db.relationship("StudentIdentifier", back_populates="student", cascade="all, delete-orphan")
    enrollments = db.relationship("StudentEnrollment", back_populates="student", cascade="all, delete-orphan")

    __table_args__ = (CheckConstraint("status IN ('active', 'inactive', 'graduated', 'withdrawn', 'deceased')", name="valid_student_status"),)


class StudentIdentifier(TimestampMixin, db.Model):
    __tablename__ = "student_identifiers"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    identifier_type = db.Column(db.String(32), nullable=False)  # PRN, admission_no, legacy_id, etc.
    identifier_value = db.Column(db.String(100), nullable=False)
    normalized_value = db.Column(db.String(100), nullable=False, index=True)
    verification_status = db.Column(db.String(24), nullable=False, default="pending")
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    source_note = db.Column(db.String(255), nullable=True)

    student = db.relationship("Student", back_populates="identifiers")
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])

    __table_args__ = (
        UniqueConstraint("identifier_type", "normalized_value", name="uq_identifier_type_normalized"),
        CheckConstraint("verification_status IN ('pending', 'verified', 'rejected')", name="valid_identifier_verification"),
    )

    @validates("identifier_type")
    def normalize_type(self, key, value):
        return value.strip().lower()

    @validates("identifier_value")
    def normalize_identifier(self, key, value):
        if not value or not value.strip():
            raise ValueError("Identifier value cannot be blank.")
        self.normalized_value = "".join(value.upper().split())
        return value.strip()


class AcademicProgram(TimestampMixin, db.Model):
    __tablename__ = "academic_programs"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), nullable=False, unique=True)
    name = db.Column(db.String(160), nullable=False)
    degree = db.Column(db.String(40), nullable=False, default="B.E.")
    duration_years = db.Column(db.Integer, nullable=False, default=4)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    __table_args__ = (CheckConstraint("duration_years BETWEEN 1 AND 10", name="valid_program_duration"),)


class CurriculumScheme(TimestampMixin, db.Model):
    __tablename__ = "curriculum_schemes"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class CurriculumVersion(TimestampMixin, db.Model):
    __tablename__ = "curriculum_versions"
    id = db.Column(db.Integer, primary_key=True)
    version_code = db.Column(db.String(64), nullable=False, unique=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey("curriculum_schemes.id"), nullable=False, index=True)
    program_id = db.Column(db.Integer, db.ForeignKey("academic_programs.id"), nullable=False, index=True)
    effective_cohort_year = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    source_filename = db.Column(db.String(255), nullable=True)
    source_sha256 = db.Column(db.String(64), nullable=True)
    source_storage_key = db.Column(db.String(255), nullable=True)
    extracted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    submitted_for_review_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approval_note = db.Column(db.Text, nullable=True)

    scheme = db.relationship("CurriculumScheme")
    program = db.relationship("AcademicProgram")
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    subjects = db.relationship("CurriculumSubject", back_populates="curriculum_version", cascade="all, delete-orphan", order_by="CurriculumSubject.semester_number, CurriculumSubject.subject_code")

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'review', 'approved', 'active', 'retired', 'rejected')", name="valid_curriculum_status"),
        CheckConstraint("effective_cohort_year BETWEEN 1990 AND 2200", name="valid_cohort_year"),
    )


class CurriculumSubject(TimestampMixin, db.Model):
    __tablename__ = "curriculum_subjects"
    id = db.Column(db.Integer, primary_key=True)
    curriculum_version_id = db.Column(db.Integer, db.ForeignKey("curriculum_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_code = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(220), nullable=False)
    semester_number = db.Column(db.Integer, nullable=False, index=True)
    credits = db.Column(db.Numeric(5, 2), nullable=True)
    course_type = db.Column(db.String(32), nullable=False, default="theory")  # theory, lab, project, elective, etc.
    assessment_structure = db.Column(db.JSON, nullable=False, default=dict)
    source_page = db.Column(db.Integer, nullable=True)
    extraction_confidence = db.Column(db.Numeric(4, 3), nullable=True)
    review_status = db.Column(db.String(24), nullable=False, default="review_required")
    notes = db.Column(db.Text, nullable=True)

    curriculum_version = db.relationship("CurriculumVersion", back_populates="subjects")

    __table_args__ = (
        UniqueConstraint("curriculum_version_id", "semester_number", "subject_code", name="uq_curriculum_semester_subject_code"),
        CheckConstraint("semester_number BETWEEN 1 AND 12", name="valid_subject_semester"),
        CheckConstraint("credits IS NULL OR credits >= 0", name="nonnegative_subject_credits"),
        CheckConstraint("review_status IN ('review_required', 'verified', 'rejected')", name="valid_subject_review_status"),
    )


class ElectiveGroup(TimestampMixin, db.Model):
    __tablename__ = "elective_groups"
    id = db.Column(db.Integer, primary_key=True)
    curriculum_version_id = db.Column(db.Integer, db.ForeignKey("curriculum_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    semester_number = db.Column(db.Integer, nullable=False, index=True)
    group_code = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    required_selections = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(24), nullable=False, default="active")
    notes = db.Column(db.Text, nullable=True)

    curriculum_version = db.relationship("CurriculumVersion")
    options = db.relationship("ElectiveOption", back_populates="elective_group", cascade="all, delete-orphan")
    selections = db.relationship("StudentElectiveSelection", back_populates="elective_group")

    __table_args__ = (
        UniqueConstraint("curriculum_version_id", "semester_number", "group_code", name="uq_elective_group_version_semester_code"),
        CheckConstraint("semester_number BETWEEN 1 AND 12", name="valid_elective_group_semester"),
        CheckConstraint("required_selections >= 1", name="positive_elective_selection_count"),
        CheckConstraint("status IN ('active', 'inactive', 'retired')", name="valid_elective_group_status"),
    )


class ElectiveOption(TimestampMixin, db.Model):
    __tablename__ = "elective_options"
    id = db.Column(db.Integer, primary_key=True)
    elective_group_id = db.Column(db.Integer, db.ForeignKey("elective_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    curriculum_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    elective_group = db.relationship("ElectiveGroup", back_populates="options")
    subject = db.relationship("CurriculumSubject")

    __table_args__ = (UniqueConstraint("elective_group_id", "curriculum_subject_id", name="uq_elective_group_subject_option"),)


class StudentEnrollment(TimestampMixin, db.Model):
    __tablename__ = "student_enrollments"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    program_id = db.Column(db.Integer, db.ForeignKey("academic_programs.id"), nullable=False, index=True)
    curriculum_version_id = db.Column(db.Integer, db.ForeignKey("curriculum_versions.id"), nullable=True, index=True)
    academic_year = db.Column(db.String(9), nullable=False, index=True)  # e.g. 2026-27
    semester_number = db.Column(db.Integer, nullable=False, index=True)
    roll_number = db.Column(db.String(32), nullable=True, index=True)
    division = db.Column(db.String(16), nullable=True, index=True)
    practical_batch = db.Column(db.String(24), nullable=True, index=True)
    enrollment_type = db.Column(db.String(24), nullable=False, default="regular")  # regular, DSE, transfer, re-admit
    enrollment_status = db.Column(db.String(24), nullable=False, default="enrolled")
    admission_year = db.Column(db.Integer, nullable=True)  # Actual admission year; never overwritten by matched curriculum cohort.
    curriculum_cohort_year = db.Column(db.Integer, nullable=True)
    enrolled_at = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    student = db.relationship("Student", back_populates="enrollments")
    program = db.relationship("AcademicProgram")
    curriculum_version = db.relationship("CurriculumVersion")

    __table_args__ = (
        UniqueConstraint("student_id", "academic_year", "semester_number", name="uq_student_term_enrollment"),
        CheckConstraint("semester_number BETWEEN 1 AND 12", name="valid_enrollment_semester"),
        CheckConstraint("enrollment_type IN ('regular', 'DSE', 'transfer', 're_admit')", name="valid_enrollment_type"),
        CheckConstraint("enrollment_status IN ('enrolled', 'detained', 'withdrawn', 'completed', 'cancelled')", name="valid_enrollment_status"),
    )


class StudentElectiveSelection(TimestampMixin, db.Model):
    __tablename__ = "student_elective_selections"
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    elective_group_id = db.Column(db.Integer, db.ForeignKey("elective_groups.id", ondelete="RESTRICT"), nullable=False, index=True)
    curriculum_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id", ondelete="RESTRICT"), nullable=False, index=True)
    selection_status = db.Column(db.String(24), nullable=False, default="selected")
    selected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    selected_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    note = db.Column(db.Text, nullable=True)

    enrollment = db.relationship("StudentEnrollment")
    elective_group = db.relationship("ElectiveGroup", back_populates="selections")
    subject = db.relationship("CurriculumSubject")
    selected_by = db.relationship("User", foreign_keys=[selected_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    __table_args__ = (
        UniqueConstraint("enrollment_id", "elective_group_id", "curriculum_subject_id", name="uq_enrollment_elective_subject"),
        CheckConstraint("selection_status IN ('selected', 'approved', 'rejected', 'withdrawn')", name="valid_elective_selection_status"),
    )


class Assessment(TimestampMixin, db.Model):
    __tablename__ = "assessments"
    id = db.Column(db.Integer, primary_key=True)
    curriculum_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id"), nullable=False, index=True)
    academic_year = db.Column(db.String(9), nullable=False, index=True)
    semester_number = db.Column(db.Integer, nullable=False)
    assessment_type = db.Column(db.String(32), nullable=False)  # CT1, CT2, midsem, assignment, lab, etc.
    title = db.Column(db.String(160), nullable=False)
    max_marks = db.Column(db.Numeric(7, 2), nullable=False)
    held_on = db.Column(db.Date, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="draft")
    subject = db.relationship("CurriculumSubject")
    created_by = db.relationship("User")
    marks = db.relationship("AssessmentMark", back_populates="assessment", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint("max_marks > 0", name="positive_assessment_max_marks"),
        CheckConstraint("status IN ('draft', 'published', 'locked', 'archived')", name="valid_assessment_status"),
    )


class AssessmentMark(TimestampMixin, db.Model):
    __tablename__ = "assessment_marks"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    marks_obtained = db.Column(db.Numeric(7, 2), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="present")  # present, absent, exempt, withheld
    entered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    corrected_from_id = db.Column(db.Integer, db.ForeignKey("assessment_marks.id"), nullable=True)
    correction_reason = db.Column(db.Text, nullable=True)
    assessment = db.relationship("Assessment", back_populates="marks")
    enrollment = db.relationship("StudentEnrollment")
    entered_by = db.relationship("User")
    __table_args__ = (
        UniqueConstraint("assessment_id", "enrollment_id", name="uq_assessment_enrollment_mark"),
        CheckConstraint("status IN ('present', 'absent', 'exempt', 'withheld')", name="valid_assessment_mark_status"),
    )


class AttendanceSession(TimestampMixin, db.Model):
    __tablename__ = "attendance_sessions"
    id = db.Column(db.Integer, primary_key=True)
    curriculum_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id"), nullable=False, index=True)
    academic_year = db.Column(db.String(9), nullable=False, index=True)
    semester_number = db.Column(db.Integer, nullable=False)
    held_on = db.Column(db.Date, nullable=False, index=True)
    division = db.Column(db.String(16), nullable=True)
    practical_batch = db.Column(db.String(24), nullable=True)
    topic = db.Column(db.String(220), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    subject = db.relationship("CurriculumSubject")
    records = db.relationship("AttendanceRecord", back_populates="session", cascade="all, delete-orphan")
    __table_args__ = (CheckConstraint("semester_number BETWEEN 1 AND 12", name="valid_attendance_semester"),)


class AttendanceRecord(TimestampMixin, db.Model):
    __tablename__ = "attendance_records"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False)  # present, absent, excused
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    session = db.relationship("AttendanceSession", back_populates="records")
    enrollment = db.relationship("StudentEnrollment")
    __table_args__ = (
        UniqueConstraint("session_id", "enrollment_id", name="uq_attendance_session_enrollment"),
        CheckConstraint("status IN ('present', 'absent', 'excused')", name="valid_attendance_status"),
    )


class ResultImport(TimestampMixin, db.Model):
    __tablename__ = "result_imports"
    id = db.Column(db.Integer, primary_key=True)
    import_code = db.Column(db.String(40), nullable=False, unique=True, index=True)
    source_filename = db.Column(db.String(255), nullable=False)
    storage_key = db.Column(db.String(255), nullable=False)
    source_sha256 = db.Column(db.String(64), nullable=False, index=True)
    university_name = db.Column(db.String(160), nullable=True)
    program_code = db.Column(db.String(24), nullable=True)
    semester_number = db.Column(db.Integer, nullable=True)
    academic_year = db.Column(db.String(9), nullable=True)
    exam_session = db.Column(db.String(80), nullable=True)
    parser_name = db.Column(db.String(100), nullable=True)
    parser_version = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="uploaded", index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    uploader = db.relationship("User", foreign_keys=[uploaded_by_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_id])
    staged_results = db.relationship("StagedResult", back_populates="result_import", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint("status IN ('uploaded', 'processing', 'review_required', 'ready_to_link', 'partially_linked', 'linked', 'rejected', 'failed')", name="valid_result_import_status"),
        CheckConstraint("semester_number IS NULL OR semester_number BETWEEN 1 AND 12", name="valid_result_import_semester"),
    )


class StagedResult(TimestampMixin, db.Model):
    __tablename__ = "staged_results"
    id = db.Column(db.Integer, primary_key=True)
    result_import_id = db.Column(db.Integer, db.ForeignKey("result_imports.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = db.Column(db.Integer, nullable=True)
    source_bbox = db.Column(db.JSON, nullable=True)
    raw_payload = db.Column(db.JSON, nullable=False, default=dict)
    extracted_prn = db.Column(db.String(100), nullable=True, index=True)
    normalized_prn = db.Column(db.String(100), nullable=True, index=True)
    extracted_subject_code = db.Column(db.String(40), nullable=True, index=True)
    extracted_subject_title = db.Column(db.String(220), nullable=True)
    extracted_marks = db.Column(db.JSON, nullable=False, default=dict)
    extracted_grade = db.Column(db.String(24), nullable=True)
    extracted_result_status = db.Column(db.String(32), nullable=True)
    confidence = db.Column(db.Numeric(4, 3), nullable=True)
    review_status = db.Column(db.String(24), nullable=False, default="pending")
    match_status = db.Column(db.String(24), nullable=False, default="unmatched")
    linked_student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True, index=True)
    linked_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id"), nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    review_note = db.Column(db.Text, nullable=True)
    result_import = db.relationship("ResultImport", back_populates="staged_results")
    linked_student = db.relationship("Student")
    linked_subject = db.relationship("CurriculumSubject")
    reviewed_by = db.relationship("User")
    __table_args__ = (
        CheckConstraint("review_status IN ('pending', 'corrected', 'approved', 'rejected')", name="valid_staged_review_status"),
        CheckConstraint("match_status IN ('unmatched', 'candidate', 'conflict', 'matched', 'linked')", name="valid_staged_match_status"),
    )


class ExamAttempt(TimestampMixin, db.Model):
    __tablename__ = "exam_attempts"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=True, index=True)
    curriculum_subject_id = db.Column(db.Integer, db.ForeignKey("curriculum_subjects.id"), nullable=False, index=True)
    source_staged_result_id = db.Column(db.Integer, db.ForeignKey("staged_results.id"), nullable=True, unique=True)
    academic_year = db.Column(db.String(9), nullable=False, index=True)
    semester_number = db.Column(db.Integer, nullable=False)
    exam_session = db.Column(db.String(80), nullable=True)
    attempt_type = db.Column(db.String(24), nullable=False, default="regular")
    internal_marks = db.Column(db.Numeric(7, 2), nullable=True)
    external_marks = db.Column(db.Numeric(7, 2), nullable=True)
    total_marks = db.Column(db.Numeric(7, 2), nullable=True)
    official_grade = db.Column(db.String(24), nullable=True)
    official_grade_point = db.Column(db.Numeric(5, 2), nullable=True)
    official_status = db.Column(db.String(24), nullable=False, default="unknown")
    effective_for_calculation = db.Column(db.Boolean, nullable=False, default=False)
    supersedes_attempt_id = db.Column(db.Integer, db.ForeignKey("exam_attempts.id"), nullable=True)
    verification_status = db.Column(db.String(24), nullable=False, default="pending")
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    source_note = db.Column(db.Text, nullable=True)
    student = db.relationship("Student")
    enrollment = db.relationship("StudentEnrollment")
    subject = db.relationship("CurriculumSubject")
    source_staged_result = db.relationship("StagedResult")
    supersedes_attempt = db.relationship("ExamAttempt", remote_side=[id], foreign_keys=[supersedes_attempt_id])
    verified_by = db.relationship("User")
    __table_args__ = (
        CheckConstraint("semester_number BETWEEN 1 AND 12", name="valid_exam_attempt_semester"),
        CheckConstraint("attempt_type IN ('regular', 'supplementary', 'revaluation', 'revised', 'other')", name="valid_exam_attempt_type"),
        CheckConstraint("official_status IN ('pass', 'fail', 'absent', 'withheld', 'provisional', 'unknown')", name="valid_exam_attempt_status"),
        CheckConstraint("verification_status IN ('pending', 'verified', 'rejected')", name="valid_exam_attempt_verification"),
    )


class AcademicPolicy(TimestampMixin, db.Model):
    __tablename__ = "academic_policies"
    id = db.Column(db.Integer, primary_key=True)
    policy_code = db.Column(db.String(64), nullable=False, unique=True)
    policy_type = db.Column(db.String(40), nullable=False)  # grading, credits, backlog, promotion, supplementary
    program_id = db.Column(db.Integer, db.ForeignKey("academic_programs.id"), nullable=True, index=True)
    curriculum_version_id = db.Column(db.Integer, db.ForeignKey("curriculum_versions.id"), nullable=True, index=True)
    effective_from_cohort_year = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(24), nullable=False, default="draft")
    rules_json = db.Column(db.JSON, nullable=False, default=dict)
    source_reference = db.Column(db.String(255), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approval_note = db.Column(db.Text, nullable=True)
    program = db.relationship("AcademicProgram")
    curriculum_version = db.relationship("CurriculumVersion")
    approved_by = db.relationship("User")
    __table_args__ = (
        CheckConstraint("policy_type IN ('grading', 'credits', 'backlog', 'promotion', 'supplementary', 'attendance')", name="valid_policy_type"),
        CheckConstraint("status IN ('draft', 'review', 'approved', 'active', 'retired', 'rejected')", name="valid_policy_status"),
    )


class CalculationSnapshot(db.Model):
    """Traceable official/provisional/policy-based calculation output.

    Inputs and result values are snapshotted so a later policy revision or
    source-data correction does not silently rewrite a previously reported value.
    """
    __tablename__ = "calculation_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=True, index=True)
    source_result_import_id = db.Column(db.Integer, db.ForeignKey("result_imports.id"), nullable=True, index=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("academic_policies.id"), nullable=True, index=True)
    supersedes_snapshot_id = db.Column(db.Integer, db.ForeignKey("calculation_snapshots.id"), nullable=True)
    calculation_type = db.Column(db.String(32), nullable=False)  # sgpa, cgpa, credits, backlogs, promotion
    source_kind = db.Column(db.String(32), nullable=False)  # official_reported, provisional, policy_calculated, approved_revised
    academic_year = db.Column(db.String(9), nullable=True)
    semester_number = db.Column(db.Integer, nullable=True)
    value_numeric = db.Column(db.Numeric(9, 3), nullable=True)
    value_label = db.Column(db.String(80), nullable=True)
    inputs_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    details_json = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(24), nullable=False, default="provisional")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    reason = db.Column(db.Text, nullable=True)

    student = db.relationship("Student")
    enrollment = db.relationship("StudentEnrollment")
    source_result_import = db.relationship("ResultImport")
    policy = db.relationship("AcademicPolicy")
    supersedes_snapshot = db.relationship("CalculationSnapshot", remote_side=[id], foreign_keys=[supersedes_snapshot_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    __table_args__ = (
        CheckConstraint("calculation_type IN ('sgpa', 'cgpa', 'credits', 'backlogs', 'promotion', 'attendance')", name="valid_calculation_type"),
        CheckConstraint("source_kind IN ('official_reported', 'provisional', 'policy_calculated', 'approved_revised')", name="valid_calculation_source_kind"),
        CheckConstraint("status IN ('provisional', 'policy_pending', 'calculated', 'approved', 'superseded', 'rejected')", name="valid_calculation_snapshot_status"),
        CheckConstraint("semester_number IS NULL OR semester_number BETWEEN 1 AND 12", name="valid_calculation_semester"),
    )


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    entity_type = db.Column(db.String(64), nullable=False, index=True)
    entity_id = db.Column(db.String(64), nullable=True, index=True)
    before_json = db.Column(db.JSON, nullable=True)
    after_json = db.Column(db.JSON, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    request_id = db.Column(db.String(64), nullable=True)
    actor = db.relationship("User")

    __table_args__ = (Index("ix_audit_entity_time", "entity_type", "entity_id", "occurred_at"),)
