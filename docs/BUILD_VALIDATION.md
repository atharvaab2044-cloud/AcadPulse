# AcadPulse — Build Validation Record

**Canonical bundle preparation:** 2026-10-23.

## Checks completed in the artifact environment

- `python -m compileall -q .`: passed after current source changes.
- Dependency-light structural, policy-gate, legacy formula-parity, spreadsheet-import-service and Jinja syntax checks, plus pure syllabus-extractor/curriculum-workflow tests: **50 passed, 69 subtests passed**. The app package initializer was shimmed only for these independent service tests because Flask is unavailable in this environment.
- All **29 Jinja templates** parsed without syntax errors.
- Template route-link audit: every literal `url_for(...)` reference in HTML templates maps to a declared blueprint route (apart from Flask's built-in `static` endpoint).
- Five spreadsheet service tests cover CSV normalization and required columns, XLSX parsing, preview-token scoping and expiration.

## Full integration test status

The full Flask/database suite has **not** been run in this artifact environment. Flask, Flask-SQLAlchemy, Flask-Migrate, Flask-Login and Flask-WTF are unavailable here, and package-index network access failed. Consequently, tests that import the application factory fail during collection in this environment. This is an environment limitation, not a passing result, and it is not represented as a full-suite pass.

After installing the core requirements on the target Windows computer, run:

```powershell
python -m pytest -q
```

Do not use real student data until the full integration suite passes locally and the workflows have been smoke-tested with fictional records. Share the complete output if any tests fail.

## Manual smoke-test checklist

1. Log in as the administrator.
2. Create a programme and a draft curriculum; upload a selectable-text syllabus PDF, inspect suggestions, verify subject fields, submit, approve, and activate.
3. Create one fictional student without PRN; add PRN, verify it, create an enrollment, then add a later term with changed roll/division/batch and confirm history is preserved.
4. Create a coursework assessment and an attendance session; enter one row manually, then preview/import a small CSV or XLSX using `student_code` or the term-specific `roll_number`.
5. Upload a small text-based university result PDF. Confirm rows stay staged until review and explicit linking. Select an effective attempt only after an active applicable policy is configured.
6. Inspect SGPA/CGPA/promotion screens. Confirm missing policy/data stays provisional or `policy_pending`; no enrollment is promoted automatically.
7. Download CSV, XLSX and PDF descriptive reports and confirm formula-like text in exported spreadsheets is stored as text.

## Required before production

- Test on the same Python/runtime and database engine selected for deployment.
- Review roles and sensitive routes; test CSRF, upload limits, private-path handling, formula-injection protections and atomic imports.
- Obtain authorized source documents and approval for grading, supplementary, credit, CGPA-revision and promotion/ATKT rules.
- Exercise backup and restore; choose durable private upload/report storage.
- Design and rehearse a separate legacy-data migration with field mapping, dry-run reports, duplicate/conflict handling, verified backup, rollback and reconciliation. Migration is not included in this bundle.
- Add deployment secrets, HTTPS, monitoring, dependency update practice, rate limits and an operational support plan before real data is used.
