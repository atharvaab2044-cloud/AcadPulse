# AcadPulse All-in-One Bundle

Bundle name: `AcadPulse_All_In_One.zip`

This is the one canonical, consolidated AcadPulse development project. Extract it into a new empty folder and open that folder in VS Code. Do not combine it with older `AcadMetrics`, `AcadPulse Starter`, `Phase2`, or per-feature update folders.

It includes the current Flask source, UI templates/styles, models, syllabus/curriculum workflow, student/enrollment workflow, electives, coursework, attendance, CSV/XLSX import previews, generic staged university-result processing, explicit linking/effective-attempt selection, policy-gated SGPA/CGPA/promotion snapshots, descriptive analytics/exports, tests and documentation.

It excludes private runtime data (`.env`, the SQLite DB, uploads, generated reports, backups), virtual environments, caches, editor files and update-patch scripts.

The bundle is a local-development MVP, not a certification of compliance with current university regulations and not production-hardened. See `docs/MVP_SCOPE_AND_LIMITATIONS.md` and `docs/BUILD_VALIDATION.md`. Run `python -m pytest -q` on the target Windows environment after installing requirements. Do not connect it to the legacy database or run a legacy migration; migration is a separate reviewed phase.
