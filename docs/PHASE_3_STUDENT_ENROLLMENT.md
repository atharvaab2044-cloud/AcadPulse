# Phase 3 — Student Identity and Semester Enrollment

## Status

Implemented in the local starter increment. Run the full test suite on the development machine before using it with real records.

## Included workflows

1. **Programme registry** — administrators can create programme records with a stable code, name, degree and duration. Programme identity is separate from curriculum version identity.
2. **Student creation** — administrators create a profile before PRN allocation. AcadPulse assigns a public student code; the database `student_id` remains the permanent internal identity. Student name, contact fields, date of birth and status can be edited without changing the code or enrollment history.
3. **PRN intake** — an administrator enters a PRN; it is stored as `pending`. The value is normalized for uniqueness checks. Another PRN already attached to any record cannot be silently reused.
4. **PRN verification** — an administrator explicitly verifies a pending PRN. The verifier and timestamp are retained. Only the `verified` state is shown as a verified identifier.
5. **Term enrollment** — each enrollment stores programme, academic year, semester, roll number, division, practical batch, admission type, enrollment status, actual admission year, curriculum cohort year and optional curriculum version.
6. **Enrollment history** — a student can have different roll numbers/divisions/batches in different terms. One record per student + academic year + semester is enforced by the database. New enrollment requests do not overwrite existing records.
7. **Regular and DSE safeguards** — the first regular enrollment is required to be Semester 1. The first DSE college enrollment is required to be Semester 3. DSE requires the curriculum cohort year to be explicitly recorded when no eligible version is selected. Actual admission year is kept separate.
8. **Curriculum association** — the enrollment form offers only `approved` or `active` curriculum versions. The selected version must belong to the chosen programme, and its effective cohort year is authoritative. Leaving the curriculum blank is allowed while academic setup is incomplete, but the UI warns that subject-level academic operations should wait.
9. **Audit trail** — create/update student, PRN submission/verification, programme creation and enrollment creation emit audit events in the same transaction as the change.
10. **Role enforcement** — viewing student records requires login; creating/editing students, adding/verifying PRNs, creating enrollments and creating programmes require the `admin` role.

## Routes

| Route | Method | Access | Purpose |
|---|---|---|---|
| `/students` | GET | Logged-in roles | Searchable paginated directory |
| `/students/new` | GET, POST | Admin | Create stable student profile |
| `/students/<id>` | GET | Logged-in roles | Profile, identifiers and enrollment history |
| `/students/<id>/edit` | GET, POST | Admin | Edit profile fields/status while preserving identity/history |
| `/students/<id>/identifiers/prn` | POST | Admin | Attach a pending PRN |
| `/students/<id>/identifiers/<identifier_id>/verify` | POST | Admin | Verify a pending PRN |
| `/students/<id>/enrollments` | POST | Admin | Create a new term enrollment |
| `/programs` | GET | Logged-in roles | Programme registry |
| `/programs/new` | GET, POST | Admin | Create a programme |

## Validation and constraints

- Student names are required and bounded; email and phone values are length/format checked.
- A PRN cannot be empty, too short, over the maximum length, or duplicated after normalization.
- An existing pending/verified PRN cannot be replaced through this standard form; corrections require a separate reviewed workflow.
- Academic years use consecutive-year notation such as `2026-27`.
- Semester numbers must be 1–12; year fields must be in the supported range.
- Student admission year cannot be changed through subsequent semester enrollments.
- Duplicate student + academic year + semester enrollment is rejected and history remains untouched.
- Roll number duplicates are checked within programme + academic year + semester + division.
- Curriculum versions must be approved/active and must match the selected programme.
- Data-changing endpoints are CSRF protected and role restricted.

## Enrollment handoff and UX refinement

- After creating the permanent profile, the application redirects directly to the `#add-enrollment` section so the next required administrative step is obvious.
- The empty-history state explains that the profile is not yet enrolled and provides a direct link to the enrollment form.
- For an existing student, the form carries forward programme, actual admission year, enrollment type, known curriculum cohort and latest known curriculum reference when available. Roll number, division and practical batch remain blank because they are term-specific.
- Semester is deliberately blank for an existing student. Staff must explicitly record the actual term rather than AcadPulse guessing semester progression or promotion eligibility.
- After enrollment saves, the application redirects to `#enrollment-history`.
- Invalid enrollment and conflict responses re-render the detail page with the submitted form data. The unsupported keyword argument in those error branches was corrected to prevent avoidable HTTP 500 errors.

## Acceptance tests

The integration suite in `tests/test_student_management.py` covers profile creation without a PRN, role restrictions, PRN pending-to-verified transition, normalized duplicate PRNs, programme creation and duplicates, DSE Semester 3 rule, actual admission vs curriculum cohort year, changed roll number history preservation, and mismatched programme/curriculum rejection. It also covers the redirect to enrollment, validation error rendering, and safe defaults for later enrollments.

## Explicitly out of scope in this phase

- Syllabus PDF extraction, curriculum draft/review/approval UI, and curriculum revision diffs.
- Student bulk import and official student-ID correction/merge workflow.
- Mark entry, attendance entry, exam result import/linking, SGPA/CGPA official outputs, and promotion/ATKT decisions.
- Non-admin/faculty permission granularity beyond the admin-only write boundary for these routes.

## Local database note

This increment does not add or change tables; it uses the existing `students`, `student_identifiers`, `student_enrollments`, `academic_programs`, `curriculum_versions`, and `audit_events` schema. Do not point the development app at the old application database. If a later change modifies model tables, use a reviewed migration rather than assuming `create_all()` updates existing schemas.
