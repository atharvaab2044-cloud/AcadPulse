# AcadPulse — phase-wise build plan

This plan defines the order, outputs, constraints and acceptance checks for building AcadPulse. A phase is complete only after its acceptance checks pass; a page that renders is not enough.

## Phase 0 — Reference lock, requirements and formula parity (requirements baseline)

**Goal:** preserve the purpose of the previous project and remove ambiguity before data is written.

**Scope**
- Use `Student-analysis-system-` as the functional, UI and calculation reference.
- Freeze the domain invariants in `REQUIREMENTS_AND_CONSTRAINTS.md`.
- Port the previous repository's `calculations.py` into `app/services/legacy_calculations.py` without changing the formula implementation.
- Add a service adapter that validates component ranges and labels previews as provisional.
- Document old behaviors and inconsistencies in `LEGACY_CALCULATION_COMPATIBILITY.md`.

**Deliverables**
- Requirements, constraints and acceptance criteria.
- Versioned legacy calculation profile ID.
- Regression tests for best-two-of-three internal calculation, theory pass rules, lab calculation, grade bands, subject inclusion, SGPA and CGPA.

**Gate**
- Legacy calculation tests pass.
- Any known mismatch between pure helpers and the old ORM updater is documented; no silent formula “fix” is made.

## Phase 1 — Clean application foundation and familiar UI (current starter)

**Goal:** a separate, safe codebase with a familiar academic-admin look and correct architectural boundaries.

**Scope**
- Flask application factory, authentication, role model, CSRF, configuration and health check.
- Base layout/navigation, responsive styles and the initial dashboard, students, curriculum, results and analytics pages.
- Initial SQLAlchemy model set: student/identifier/enrollment, programme/scheme/curriculum, assessments/attendance, result imports/staged results/exam attempts, policies/calculation snapshots and audit events.
- SQLite for local development/tests; PostgreSQL for shared/production.
- No connection to the legacy database; no real-record migration yet.

**Gate**
- App starts locally, admin creation is explicit, `.env` is not committed.
- All current routes are protected appropriately; no decorative metric is represented as real data.
- Application and ORM tests run after dependencies are installed.

## Phase 2 — Academic setup and syllabus-to-curriculum workflow

**Current implementation status:** the first conservative text-based workflow is implemented in `app/blueprints/curricula.py`, `app/services/syllabus_extractor.py` and `app/services/curriculum_workflow.py`. Full Flask/database integration tests must still be run on the target machine. OCR, broad layout adaptation, richer structured assessment extraction and side-by-side page-image annotation remain future refinements.

**Goal:** approved curriculum is the source of subject structure.

**Build**
- Manage programme/branch and the scheme registry (NEP/MIS is one scheme code).
- Upload syllabus PDFs to generated storage names, hash for duplicate identification and profile digital/scanned pages.
- Current parser: suggest subject code/title, semester, clearly labelled credits when detectable, heuristic course type, source-page excerpt and parser confidence. These are suggestions, not certified facts.
- Human review in this increment: confirm code/title/semester/credits/course type; manually configure structured assessment components and maxima. Elective-group editing and richer assessment/elective extraction are future sub-features, not claimed as implemented by the current text parser.
- Store extracted content as a draft; let an administrator correct fields and view page evidence.
- On re-upload, compare with a chosen prior version and show added/changed/removed/unchanged records.
- Approve and activate immutable curriculum versions; never overwrite a version already used by enrollments.
- Use a syllabus-specific service, not the university-result parser. Shared PDF libraries are utilities, not shared parsing logic.

**Gate**
- Upload/extraction alone cannot change the active curriculum.
- Unknown or low-confidence fields remain review-required, not silently defaulted.
- Every active subject has version scope and approved assessment maxima.

## Phase 3 — Student identity and term enrollment

**Goal:** every term's records attach to the right human without losing history.

**Build**
- Create the stable internal student code before a PRN exists.
- Create/verify PRN and other identifiers later with duplicate/conflict handling.
- Create term enrollment with program, academic year, semester, curriculum version, roll number, division, practical batch, type and status.
- Support regular students, DSE, detained, transfer and re-admission.
- Record admission year separately from curriculum cohort year.
- Provide search by name, stable code, verified PRN and current/historical roll number.
- Audit merges, identifier verification and sensitive edits.

**Gate**
- Changing roll/division/batch creates/updates the relevant term enrollment without changing the permanent student ID.
- PRN assignment attaches to the existing student instead of duplicating the student.
- DSE students start at Semester 3 unless recognized earlier records are deliberately imported.
- Names alone never auto-link identities.

## Phase 4 — Coursework marks and attendance

**Goal:** safe assessment entry and dependable component-level data.

**Build**
- Configure assessment templates from the approved curriculum.
- Support CT1, CT2, Assignment, mid-semester, lab/project components and other configured items.
- Use range validation against configured component maxima, row-level spreadsheet previews and explicit error correction.
- Store `present`, `absent`, `exempt`, `withheld` and `not entered` distinctly.
- Correct marks with a reason and retained before/after audit evidence.
- Attendance sessions by subject/date/term/division/batch; one attendance record per enrollment/session.
- Ensure permitted faculty can edit only their authorized academic scope.

**Gate**
- Invalid maxima and duplicate rows are rejected before database commit.
- Missing marks are not treated as zero or as a pass/fail result.
- Upload retry cannot silently double attendance or marks.

## Phase 5 — Calculation engine: old formula parity + traceable outputs

**Goal:** preserve the previous repository's calculations and isolate future rule changes.

**Build**
- Use `legacy_mark_service.py` as the single adapter over `legacy_calculations.py`.
- Internal theory = best two from CT1/CT2/Assignment + Midsem; theory total = internal /40 + external /60; theory pass requires total >=40 and external >=20.
- Lab = CA1 /30 + CA2 /30 + external /40; lab pass requires total >=40; retain the previous-internal fallback only for recognized legacy records.
- Use the prior grade table, gradable subject filter, credit counting, SGPA/CGPA helpers and legacy rounding behavior in parity tests.
- Produce a calculation snapshot with inputs, marks source, calculation profile/version, policy version, output and status.
- Distinguish a preview from an authoritative result. Official mode requires an active AcademicPolicy explicitly mapped to `dbatu_ece_legacy_calculations_v1`.
- Build a new-schema SGPA/CGPA adapter; do not call the old ORM updater directly.

**Gate**
- Golden test fixtures match outputs from the prior repository for all agreed examples and grade boundaries.
- Failed subjects register credits but earn zero credits; passed subjects earn their configured credits.
- Excluded subjects (audit, attendance-only, inactive, nonpositive credits, elective parent/container) do not contribute.
- Missing marks remain pending according to the chosen semester-completeness rule.
- Existing pure-helper/updater inconsistency is resolved through an explicit documented decision before official integration.

## Phase 6 — University-result PDF import and approved linking

**Goal:** import is safe, explainable and independent from course/curriculum setup.

**Build**
- Store uploaded file in generated/private storage with SHA-256, scope and job status.
- Profile each PDF; use a parser registry including the legacy DBATU Crystal adapter, generic digital text/table fallback, and OCR for scanned PDFs.
- Keep source filename/hash, parser/version, raw payload, normalized values, page/bounding-box evidence, confidence and validation messages.
- Analyze staged results without linking them to official student history.
- Show reviewer comparison against student/enrollment/subject records and prevent duplicate/conflicting matches.
- On explicit approval, append an `ExamAttempt`; never overwrite the regular attempt when supplementary/revaluation/revised data arrives.

**Gate**
- Repeated uploads detected; source PDFs remain traceable.
- Unknown formats can fail/review gracefully rather than inventing columns.
- No student or official result changes before explicit authorized linking.
- Revised/supplementary attempts preserve all earlier attempts.

## Phase 7 — Supplementary/backlog and progression policy

**Goal:** implement institutional rules only after they are explicitly approved.

**Build**
- Capture source document/decision, university/scheme/programme/cohort scope, effective date, approval, policy version and test examples.
- Define effective attempt, internal-mark carry-forward, pass/credit counting, backlog clearing, SGPA/CGPA revision and promotion/ATKT independently.
- Review revisions with before/after previews; snapshots keep the previously published values and history.
- Keep promotion manual and label results `policy_pending` until the applicable policy is approved and active.

**Gate**
- No guessed 80% threshold or assumed “latest/highest attempt wins” logic.
- Passing supplementary can clear the backlog only under the configured approved rule; credits count once per subject per applicable scheme rule.
- Policy revisions create new calculation snapshots rather than mutating prior audit evidence.

## Phase 8 — Analytics, reports and exports

**Goal:** decision-useful analytics with definitions and provenance.

**Build**
- Student, subject, semester, programme/branch, cohort, division, batch, assessment, attendance, backlog and progression filters.
- Define denominator, pending/absent exclusions, mark normalization and tie handling for every metric.
- Charts and KPIs derived from real database queries, not sample constants.
- Excel/PDF exports include report timestamp, term, filters, record count and calculation/policy version where relevant.

**Gate**
- Dashboard values reconcile to query fixtures.
- Subject comparisons do not accidentally compare different scales/maximums as if equal.
- Access controls apply to queries as well as pages.

## Phase 9 — Security, migration rehearsal and release

**Goal:** deploy without corrupting or exposing student records.

**Build**
- Establish Alembic baseline and reviewed migrations; shared/production use PostgreSQL.
- Rate-limit/login hardening as appropriate, safe upload limits, file-type checks, private storage, dependency review and generic error pages.
- Add unit/integration/end-to-end tests, role matrix, performance tests for key lists/reports, backup/restore drill and PostgreSQL CI.
- Design a separate legacy-data import mapping with dry-run, conflict report, counts/checksums and explicit go/no-go approval.
- Create deployment/runbook and recovery steps.

**Gate**
- Verified backup restores into an isolated environment.
- No `.env`, real database, result PDF or real student data in Git.
- Migration rehearsal passes on a copy, not on the live legacy database.

## Dependency / release rule

Phases are sequential acceptance gates, though individual screens can be built in parallel after their models and contracts are agreed. Do not skip a gate because a page appears to work. Mark calculations, curriculum activation and result linking are high-risk transitions and require tests plus audit provenance.

## Current working increment — first student-management slice

The code now includes the initial functional part of Phase 3, implemented early so the identity and enrollment design can be exercised end-to-end: programme registry, student profile create/edit, PRN pending/verify workflow, term enrollment creation, regular/DSE intake guards, programme/curriculum consistency checks, role-protected writes and audit events. This does **not** mean full Phase 2 or Phase 3 is complete. Syllabus PDF extraction, curriculum review/activation, bulk import and identity-correction workflows remain unbuilt. Marks, attendance and official results must remain unavailable for a cohort until its applicable curriculum and academic policy are approved.

Detailed route and validation scope: `PHASE_3_STUDENT_ENROLLMENT.md`.


## Consolidated bundle status (2026-10-23)

The current one-ZIP MVP contains the implemented workflows summarized in `MVP_SCOPE_AND_LIMITATIONS.md`. In practical terms: student/enrollment, first-pass syllabus-to-curriculum, electives, coursework, attendance, spreadsheet preview imports, university-result staging/linking, calculation snapshots/policy gates, and descriptive exports are represented in the source. The following remain partial or release work: scanned-PDF OCR; robust tables/layout support and certification of a DBATU-specific parser; official institution-specific supplementary/backlog/credit/promotion rules; old-database migration; and production security/backup/restore verification. All Flask/SQLAlchemy integration tests must be run in the target environment after dependency installation. A module is not declared production-ready just because its route or template exists.
