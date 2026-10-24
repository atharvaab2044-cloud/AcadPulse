# AcadPulse — Academic Records & Analytics

AcadPulse is a modular academic records and analytics application for an engineering college. It brings student identity/history, curriculum, electives, coursework, attendance, university-result review, calculation snapshots, policy management, audit records and descriptive exports into one Flask application.

**Use this project ZIP as the single canonical codebase.** The earlier `AcadMetrics Next`, `AcadPulse Starter`, and per-phase update ZIPs are not needed. Keep any older folders as reference only; do not merge them into this one.

Reference repository: [Student-analysis-system-](https://github.com/shindetanay316-lab/Student-analysis-system-). The new code follows its academic-administration purpose and familiar UI direction while separating identity, enrollment, curriculum revisions, result staging and calculations.

## Implemented in this MVP

- **Application foundation:** Flask app factory, login/logout, password hashing, roles, CSRF, local SQLite, admin creation, audit log and policy registry.
- **Student identity and enrollment:** stable internal identity and student code; pending/verified PRN; programme registry; enrollment per academic year/semester; term-specific roll, division and batch; regular/DSE intake checks; enrollment history.
- **Curriculum setup:** programme/scheme registry; syllabus PDF intake; conservative text-based subject suggestions; source hash and page evidence; editable draft subjects; added/changed/removed/unchanged comparison; submit/review/approve/activate lifecycle; private source storage.
- **Electives:** elective groups/options, student selections and approval records.
- **Coursework:** assessment creation, roster-based mark entry/correction, statuses, lock/publish workflow, audit records, subject summaries and CSV/XLSX import preview plus atomic commit.
- **Attendance:** sessions by subject/date/term/division/batch, roster marking/correction, attendance percentage summaries, CSV/XLSX import preview plus atomic commit and CSV export.
- **University results:** PDF intake and hash-based duplicate detection; generic text-based candidate extraction; staged row edit/review/reject; explicit linking to a student/subject/enrollment; append-only exam-attempt history.
- **Calculations:** previous repository's calculation functions and grade table are retained in a versioned compatibility profile; theory/lab previews; policy-gated SGPA snapshots; CGPA from approved SGPA snapshots; promotion evaluation/snapshots; admin approval and audit trail.
- **Analytics and exports:** descriptive academic overview with CSV, XLSX and PDF exports.
- **Testing/documentation:** unit/structural, formula parity, curriculum, student workflow and integration tests, data dictionary, requirements, architecture, calculation notes and roadmap.

## Important limitations (do not mistake this MVP for a production-certified SIS)

1. The syllabus extractor currently handles selectable-text PDFs conservatively. Scanned-page OCR, robust table understanding and automatic structured assessment extraction are not implemented. Every extracted subject is a suggestion and requires review.
2. The university-result parser is a generic candidate extractor, not a universal or fully DBATU-layout-certified parser. It preserves candidate source lines; marks/status must be reviewed and corrected before linking. Unknown/scanned layouts may produce no candidates.
3. Bulk imports accept UTF-8 CSV and XLSX, use a preview/review stage and never match by name. Legacy XLS and macro-enabled XLSM files are not accepted.
4. The previous repository's calculation behavior is retained for compatibility, not certified as a current university regulation. Fractional grade-band gaps and legacy missing-result behavior remain documented. Official calculations require a properly scoped active policy. Promotion/ATKT remains pending without an approved rule.
5. The result import, spreadsheet, calculation and reporting workflows need a full integration run on the target computer. This build environment could not install Flask dependencies because the package index was unreachable. See `docs/BUILD_VALIDATION.md`.
6. No old database is imported automatically. Do not connect this new project to the legacy database until a reviewed mapping, migration script, dry run, backup and restore test exist.
7. The project is a local development MVP. Before real deployment, add deployment secrets, HTTPS, backup/restore exercises, monitoring, rate limits, tested database migrations, permission review and an operational support plan.

## One-time install on Windows

See [`INSTALLATION_WINDOWS.md`](INSTALLATION_WINDOWS.md) for the full step-by-step guide. From the extracted project folder (the folder containing `run.py`):

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated value into `SECRET_KEY` in `.env`. Do not share or commit `.env`.

Initialize only this new, blank development database and create the administrator:

```powershell
flask --app run.py init-db
flask --app run.py create-admin
python -m pytest -q
python run.py
```

Open `http://127.0.0.1:5000`. Core install includes PyMuPDF, openpyxl and ReportLab. Additional table/data libraries can be installed with `python -m pip install -r requirements-document-processing.txt`; this does not add OCR.

## Daily startup

Each time you open VS Code, open this project folder, then run:

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

Alternatively, after setup, double-click `START_ACADPULSE.bat` to launch the server or `TEST_ACADPULSE.bat` to run tests. Stop the development server using `Ctrl+C`. **Do not run `init-db` again** once the database exists. Back up `.env` and `instance/` privately; neither is distributed in the ZIP.

## Tests

Run the full suite after installing `requirements.txt`:

```powershell
python -m pytest -q
```

The suite contains tests that require Flask/SQLAlchemy plus dependency-light structural, parser and calculation tests. A green run on the target computer is the required integration check; do not assume the full suite passed merely because the package was generated.

## Domain invariants

- Student ID is stable; PRN is a separately verified identifier; roll/division/batch are term-specific.
- NEP/MIS is one scheme identity.
- DSE keeps actual admission year and begins college academic history at Semester 3 unless verified prior records are intentionally imported.
- Curriculum PDF extraction only creates a draft; activation is explicit; versions are retained.
- Imported results are independent from official student records until reviewed and linked by an authorized user.
- Regular, supplementary, revaluation and revised exam attempts remain in history.
- Result-to-attempt linking does not automatically select an effective attempt. Effective-attempt selection requires an active, scoped rule and administrator reason.
- Missing or ambiguous policy means `policy_pending`; promotion status is not changed automatically.
- Legacy calculation behavior is versioned and must not be silently modified.

## Main documentation

- `docs/MVP_SCOPE_AND_LIMITATIONS.md`
- `docs/REQUIREMENTS_AND_CONSTRAINTS.md`
- `docs/PHASE_WISE_ROADMAP.md`
- `docs/LEGACY_CALCULATION_COMPATIBILITY.md`
- `docs/DATA_DICTIONARY.md`
- `docs/ARCHITECTURE.md`
- `docs/BUILD_VALIDATION.md`
