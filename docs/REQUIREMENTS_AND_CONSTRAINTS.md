# AcadPulse — functional requirements, constraints and invariants

## Product purpose

AcadPulse is an academic records management and analytics system for an engineering college. It combines student identity/history, academic structure, curriculum, elective selection, coursework marks, attendance, university-result intake/review, transparent calculations, analytics and reports. It is not only a dashboard and it is not permitted to silently turn extracted or provisional data into an official academic record.

## Functional requirements (FR)

### FR-01 Identity and access
- Support administrator, faculty and viewer roles.
- Keep password hashes only; require CSRF protection for state-changing browser forms.
- Enforce permission checks in server routes/services, not only by hiding UI controls.
- Audit consequential changes with actor, timestamp, entity, before/after evidence when appropriate, and reason.

### FR-02 Student identity
- Use stable internal `student_id` as the relationship key.
- Allow student creation before a PRN exists.
- Store PRN, admission code and other institutional identifiers separately with verification status and duplicate checks.
- Never resolve a student's identity using name alone.
- Preserve identifiers and history rather than replacing identity when values change.

### FR-03 Term enrollment
- Store roll number, division, practical batch, academic year, semester, programme, curriculum version, admission year and enrollment type/status on each enrollment.
- Support regular, DSE, detained, transfer and re-admitted cases.
- A DSE student's actual admission year remains unchanged; normal college academic history starts at Semester 3 unless validated prior records are provided.
- A repeated semester in a later academic year is a new enrollment; it does not overwrite the earlier term.

### FR-04 Academic structure and curriculum
- Manage programme/branch and scheme. NEP/MIS is one scheme identity (`NEP_MIS`) based on the prior repository's faculty decision.
- Upload syllabus PDF into a draft pipeline; extract subjects, codes, titles, semester, credits, course types, assessment structures and source-page evidence.
- Require human review and explicit approval before a curriculum version becomes active.
- Re-upload creates a revision/diff (added, modified, removed, unchanged); an already-used version cannot be mutated in place.
- Elective group parent/container rows are not gradable subjects; only valid selected options participate in academic calculations.

### FR-05 Coursework and attendance
- Support CT1, CT2, Assignment, Mid-semester, lab/project components and future curriculum-configured assessments.
- Validate numeric values against each assessment's configured maximum before saving.
- Represent absent, exempt, withheld and not-yet-entered separately; do not quietly convert them to zero.
- Support spreadsheet import preview, row-level errors, corrections, duplicate detection and audit history.
- Support attendance sessions by subject/date/term/division/batch and attendance entries per enrollment/session.

### FR-06 Legacy mark calculation compatibility
- Use formulas and grading behavior from the owner-provided `Student-analysis-system-/calculations.py` as the initial DBATU/ECE compatibility profile.
- Keep the old formulas in a named, versioned service; tests must compare representative cases against the old source behavior.
- Preserve the previous theory, lab, grade-table, subject-eligibility, SGPA and CGPA calculations plus internal-assessment summary thresholds described in `LEGACY_CALCULATION_COMPATIBILITY.md`.
- Keep provisional previews distinct from authoritative calculations. An authoritative result requires an active academic policy whose `rules_json.calculation_profile` explicitly maps to `dbatu_ece_legacy_calculations_v1`.
- Do not silently change legacy edge behavior. Log known inconsistencies and require a deliberate, tested versioned change after faculty confirmation.

### FR-07 University result intake
- Accept a result PDF as an independent import. Record file hash, academic context, parser and version.
- Keep original file metadata, raw extracted values, normalized values, page/bounding-box evidence and confidence.
- Separate syllabus extraction from university result parsing, even when they share PDF libraries/OCR utilities.
- Support a DBATU Crystal adapter, generic text/table extraction, and OCR fallback for scanned PDFs; no promise of perfect universal parsing.
- Analyze staged results without modifying student history. Explicit authorized linking is required to create `ExamAttempt` records.
- Preserve regular, supplementary, revaluation and revised attempts. A later attempt never overwrites the earlier one; credit and effective-attempt rules come from approved policy.
- Flag conflicting PRN/programme/branch/semester/subject context for review; never link by name alone.

### FR-08 Policies and calculations
- Store grading, credit, supplemental-attempt, backlog and promotion policies with scope, version/effective cohort, status, source reference and approval metadata.
- Distinguish official grades/result figures from locally computed provisional figures.
- Persist calculation snapshots with inputs, result, formula/policy version, timestamp and supersession history.
- If policy is missing, inactive, empty, conflicting or not applicable, return `policy_pending` for authoritative operations.
- Do not use an unconfirmed 80% promotion threshold or hard-code an ATKT/promotion rule.

### FR-09 Analytics and reports
- Filter analyses by programme, scheme/curriculum, cohort, academic year, semester, subject, division, batch, assessment and status where applicable.
- Label metric definitions, scope, excluded/pending records and calculation version.
- Offer Excel/PDF exports with filters, report timestamp, selected term, record counts and provenance.
- Dashboard KPIs derive from stored records; do not display fabricated operational counts.

## Non-functional requirements (NFR)

- NFR-01 Integrity: foreign keys, unique constraints, check constraints and transactional writes for academic records.
- NFR-02 Traceability: retain provenance, corrections, result attempts, calculation inputs and approvals.
- NFR-03 Security: private config, safe upload storage, CSRF protection, role checks, input validation, secret management and limited error disclosure.
- NFR-04 Reliability: automated tests for formulas, workflows, migrations, backups, restore and duplicate import behavior.
- NFR-05 Maintainability: Flask modular monolith; business logic in services rather than route handlers; Alembic migrations rather than direct production `create_all`.
- NFR-06 Portability: Windows/macOS/Linux local development; SQLite for local/test use and PostgreSQL as shared/production target.
- NFR-07 Usability: familiar left-navigation academic-admin UI based on the existing repository's look, with clearer forms, status labels, reviews, bulk import and responsive layout.
- NFR-08 Accessibility: keyboard operability, visible focus, labels and text/icon status cues, not color alone.
- NFR-09 Data minimization: no student records, `.env`, database dumps or uploaded confidential results in source control.

## Constraints and non-negotiable decisions

1. Build a clean codebase, keep the previous repository untouched as the functional/formula/UI reference.
2. Preserve familiar visual language rather than copying every pixel or switching to an unrelated design system.
3. Begin as a modular monolith; do not split into microservices prematurely.
4. Do not connect the new database directly to the old application's configured database.
5. No real student data migration until there is a reviewed, tested mapping and restore rehearsal.
6. DBATU Crystal reports are a supported parser family, not a universal format.
7. Curriculum activation and result linking require explicit approval.
8. Syllabus extraction, university-result extraction, calculations and promotion decisions are separate responsibilities.
9. Legacy calculations are carried forward under a named compatibility profile. Existing edge cases are documented, not silently changed.
10. The rules for promotion/ATKT, supplementary effective attempt and credit counting remain unapproved until official faculty policy is supplied.

## Acceptance gates

No phase is considered complete solely because a page renders. Each must meet its testable acceptance criteria in `PHASE_WISE_ROADMAP.md`; calculation-affecting changes additionally require parity/regression cases and recorded profile/policy version.
