# Core data dictionary — first schema increment

The following explains the scope of fields in the first schema. Columns may be extended after requirements are confirmed, but a field must not silently take on a different meaning.

## Cross-cutting fields

- `id`: internal database primary key, used for relationships. Not a roll number or PRN and not shown as the student's institutional identifier.
- `created_at`, `updated_at`: UTC timestamps for record creation and last ordinary update. Audit events separately record who changed what and why.
- `status`: lifecycle state. Allowed values are constrained per table; do not invent meanings ad hoc in routes.
- `notes` / `approval_note` / `review_note`: contextual free text, not a substitute for structured state or audit evidence.

## Identity and access

### `users`

- `email`: unique login identifier.
- `full_name`: display name.
- `password_hash`: Werkzeug password hash only; raw passwords are never stored.
- `role`: `admin`, `faculty` or `viewer`. New write routes must enforce authorization on the server.
- `is_active`: permits temporary access suspension without deleting the user's audit history.

### `students`

- `student_code`: stable application-generated code. Used to find a student before PRN allocation.
- `full_name`, `email`, `phone`, `date_of_birth`: profile attributes; optional contact/biographical data must be access-controlled.
- `status`: `active`, `inactive`, `graduated`, `withdrawn` or `deceased`.
- `id`: stable internal identity. All academic record foreign keys use this rather than roll number.

### `student_identifiers`

- `student_id`: the stable student profile to which an identifier belongs.
- `identifier_type`: normalized type, e.g. `prn`, `admission_no`, `legacy_id`.
- `identifier_value`: displayed/source value, retained as supplied after trimming.
- `normalized_value`: whitespace-free uppercase comparison value used to detect duplicates.
- `verification_status`: `pending`, `verified` or `rejected`.
- `verified_by_id`, `verified_at`: verification provenance.
- `source_note`: where the identifier came from or why it was corrected.
- Scope: an identifier value is unique within its identifier type. A matching name alone never proves identity.

## Academic structure

### `academic_programs`

- `code`: stable short programme/branch code.
- `name`: display label.
- `degree`: award type, e.g. B.E.
- `duration_years`: nominal programme duration, not a promotion rule.
- `is_active`: whether new setup workflows may select it.

### `curriculum_schemes`

- `code`: unique scheme identity. NEP and MIS are represented as one scheme `NEP_MIS` based on the recorded faculty decision.
- `display_name`: user-facing label.
- `description`, `is_active`: optional notes and current availability.

### `curriculum_versions`

- `version_code`: unique, immutable internal revision identifier, e.g. `NEP_MIS_2026` until an official code is supplied.
- `scheme_id`, `program_id`: academic scheme and programme scope.
- `effective_cohort_year`: cohort/admission year the version is intended for; for DSE this is not the student's actual admission year.
- `status`: `draft`, `review`, `approved`, `active`, `retired` or `rejected`.
- `source_filename`, `source_sha256`, `source_storage_key`: provenance for the syllabus PDF.
- `extracted_at`, `submitted_for_review_at`: workflow timestamps.
- `approved_by_id`, `approved_at`, `approval_note`: approval provenance.
- Scope: activation controls future use. Re-upload creates a new draft version and never overwrites a version used by existing records.

### `curriculum_subjects`

- `curriculum_version_id`: the precise curriculum revision this subject belongs to.
- `subject_code`, `title`: source and display identity for the subject.
- `semester_number`: semester in that curriculum version.
- `credits`: nullable until extraction is verified; zero or missing credit must not be inferred.
- `course_type`: category such as theory, lab, project or elective.
- `assessment_structure`: structured JSON for component names and maxima; extracted data is still a draft until reviewed.
- `source_page`, `extraction_confidence`: page evidence and extraction confidence.
- `review_status`: `review_required`, `verified` or `rejected`.
- Unique scope: subject code is unique within one curriculum version and semester.

## Term-scoped student records

### `student_enrollments`

- `student_id`: stable student profile.
- `program_id`: enrolled programme/branch.
- `curriculum_version_id`: version used for the term; may be empty until assigned.
- `academic_year`: teaching/examination session, e.g. `2026-27`.
- `semester_number`: semester in this term.
- `roll_number`, `division`, `practical_batch`: term-specific values. Changing them for a later term creates a new enrollment row and keeps history.
- `enrollment_type`: `regular`, `DSE`, `transfer` or `re_admit`.
- `enrollment_status`: `enrolled`, `detained`, `withdrawn`, `completed` or `cancelled`.
- `admission_year`: actual year admitted to the college.
- `curriculum_cohort_year`: regular cohort year whose curriculum applies; may differ from admission year for DSE.
- `enrolled_at`, `notes`: enrollment date and case context.
- Uniqueness: one row per student, academic year and semester. A detained repeat in a later year is a separate term.

### `elective_groups`, `elective_options` and `student_elective_selections`

- `elective_groups`: group belongs to a curriculum version and semester; `group_code` is unique within that scope; `required_selections` states how many options a student may/should select under the approved curricular setup.
- `elective_options`: ties an allowed `curriculum_subject_id` to an elective group; a subject is only an option in that group, not automatically enrolled by listing it.
- `student_elective_selections`: records the selected subject against a term enrollment and group; `selection_status` captures selected/approved/rejected/withdrawn and `selected_by_id`/`approved_by_id` record provenance.
- Cross-record validation must ensure the enrollment and subjects use compatible curriculum versions and semester. The service layer must enforce the group's selection count; the database's unique key prevents duplicate selection of the same subject within the same group.

## Internal coursework and attendance

### `assessments`

- `curriculum_subject_id`: exact subject/version component being assessed.
- `academic_year`, `semester_number`: assessment context.
- `assessment_type`, `title`: type and readable name (CT1, CT2, midsem, lab, project, etc.).
- `max_marks`: maximum attainable mark, must be positive.
- `held_on`, `created_by_id`: assessment date and owner.
- `status`: `draft`, `published`, `locked` or `archived`.

### `assessment_marks`

- `assessment_id`, `enrollment_id`: whose mark for which assessment.
- `marks_obtained`: may be null when not entered; absence should be represented by status, not silently converted.
- `status`: `present`, `absent`, `exempt` or `withheld`.
- `entered_by_id`: staff provenance.
- `corrected_from_id`, `correction_reason`: correction chain and required reason; correction handling must not erase the original audit evidence.

### `attendance_sessions` and `attendance_records`

- Session: `curriculum_subject_id`, `academic_year`, `semester_number`, `held_on`, `division`, `practical_batch`, `topic`, `created_by_id`.
- Record: `session_id`, `enrollment_id`, `status` (`present`, `absent`, `excused`), `marked_by_id`.
- Scope: one attendance record per student enrollment per session; percentages are derived from sessions and status, not stored as the source of truth.

## University results

### `result_imports`

- `import_code`: unique human-readable job/import identifier.
- `source_filename`, `storage_key`, `source_sha256`: original document provenance, internal storage address and duplicate detection.
- `university_name`, `program_code`, `semester_number`, `academic_year`, `exam_session`: one import's academic context. A PDF mixing branches or semesters must be flagged.
- `parser_name`, `parser_version`: extraction implementation provenance.
- `status`: upload/processing/review/linking/failure lifecycle.
- `uploaded_by_id`, `uploaded_at`, `reviewed_by_id`, `reviewed_at`, `notes`: workflow and approval context.

### `staged_results`

- `result_import_id`: source import.
- `page_number`, `source_bbox`: source page and optional bounding-box evidence.
- `raw_payload`: source-oriented extraction that should not be silently rewritten.
- `extracted_prn`, `normalized_prn`: PRN as extracted and comparison form.
- `extracted_subject_code`, `extracted_subject_title`: subject match candidates.
- `extracted_marks`, `extracted_grade`, `extracted_result_status`: normalized extraction payload; not automatically authoritative.
- `confidence`: extraction confidence, not confidence that a student identity is correct.
- `review_status`: `pending`, `corrected`, `approved` or `rejected`.
- `match_status`: `unmatched`, `candidate`, `conflict`, `matched` or `linked`.
- `linked_student_id`, `linked_subject_id`: candidates/approved association; must be set only by a validated workflow.
- `reviewed_by_id`, `reviewed_at`, `review_note`: reviewer provenance.
- Scope: staged records are independent from official academic history until explicit approval links them.

### `exam_attempts`

- `student_id`: linked permanent identity.
- `enrollment_id`: applicable term enrollment when known.
- `curriculum_subject_id`: exact subject/version.
- `source_staged_result_id`: originating staged row, unique when present.
- `academic_year`, `semester_number`, `exam_session`: exam scope.
- `attempt_type`: `regular`, `supplementary`, `revaluation`, `revised` or `other`.
- `internal_marks`, `external_marks`, `total_marks`: official/source components stored separately.
- `official_grade`, `official_grade_point`, `official_status`: values reported/verified from the official source.
- `effective_for_calculation`: selection marker whose behavior must be controlled by the approved policy service; never infer highest/latest attempt without a rule.
- `supersedes_attempt_id`: lineage for a revised attempt; the previous attempt remains stored.
- `verification_status`, `verified_by_id`, `verified_at`, `source_note`: approval provenance.
- Credit counting must be policy-driven; a supplementary pass must not count credits twice.

## Policies and audit

### `academic_policies`

- `policy_code`: unique versioned policy identifier.
- `policy_type`: grading, credits, backlog, promotion, supplementary or attendance.
- `program_id`, `curriculum_version_id`: applicability scope; null means broad scope only if policy semantics explicitly allow it.
- `effective_from_cohort_year`: cohort cutoff.
- `status`: draft, review, approved, active, retired or rejected.
- `rules_json`: versioned machine-readable rules, never a dumping ground for unvalidated arbitrary text.
- `source_reference`, `approved_by_id`, `approved_at`, `approval_note`: source and approval trace.

### `calculation_snapshots`

- `student_id`, `enrollment_id`: identity and term when applicable.
- `source_result_import_id`: source report, when the value came from an imported result.
- `policy_id`: policy used for a policy-based calculation; null for values explicitly reported by a university or ungoverned provisional analysis.
- `supersedes_snapshot_id`: links a correction/recalculation to an earlier snapshot without deleting it.
- `calculation_type`: SGPA, CGPA, credits, backlogs, promotion or attendance.
- `source_kind`: `official_reported`, `provisional`, `policy_calculated` or `approved_revised`.
- `academic_year`, `semester_number`: time scope; CGPA may span multiple terms.
- `value_numeric`, `value_label`: numeric result or a textual state such as `policy_pending`.
- `inputs_snapshot`, `details_json`: immutable record of the inputs/rules/derivation needed to explain the output.
- `status`: `provisional`, `policy_pending`, `calculated`, `approved`, `superseded` or `rejected`.
- `created_by_id`, `approved_by_id`, `approved_at`, `reason`: operation and approval trace.
- Scope: a changed source attempt or policy creates a new snapshot; it does not silently rewrite a previously produced result.

### `audit_events`

- `occurred_at`: event time.
- `actor_id`: user who performed the operation, if known.
- `action`: controlled action code such as `student_identifier_verified` or `exam_attempt_linked`.
- `entity_type`, `entity_id`: changed entity reference.
- `before_json`, `after_json`: minimal change snapshot, avoiding unnecessary sensitive data.
- `reason`: mandatory for defined correction/override operations.
- `request_id`: joins related events from one workflow.

## Invariants to test before implementation grows

1. Student database identity never derives from PRN or roll number.
2. A roll-number change in a new enrollment does not mutate earlier enrollment history.
3. DSE preserves actual admission year but does not fabricate Semesters 1–2 history.
4. A draft syllabus never activates itself and never mutates a previously active curriculum.
5. A result import never creates an `ExamAttempt` until an authorized link approval succeeds.
6. Supplementary and revised attempts are append-only history.
7. Missing or inactive policy blocks official SGPA/CGPA and promotion claims.
8. Every consequential correction/link/approval is auditable.
