# AcadPulse implementation plan

The authoritative detailed sequence, requirements, constraints and acceptance checks are in [`PHASE_WISE_ROADMAP.md`](PHASE_WISE_ROADMAP.md). Cross-module requirements live in [`REQUIREMENTS_AND_CONSTRAINTS.md`](REQUIREMENTS_AND_CONSTRAINTS.md). Calculation parity requirements are in [`LEGACY_CALCULATION_COMPATIBILITY.md`](LEGACY_CALCULATION_COMPATIBILITY.md).

Current build status:
- Clean Flask application shell, configuration, login/roles, core data model, UI shell and workspace read pages exist.
- Legacy mark formulas from the previous repository have been ported as a named compatibility profile and exposed through a preview adapter.
- Pure-function regression tests cover the copied formulas and calculation adapter.
- Syllabus PDF upload, text extraction suggestions, source/hash preservation, manually editable subjects, readiness validation, revision comparison, explicit submit/approve/activate actions and audit events are now implemented. Scanned/low-text PDFs are flagged for manual entry; OCR is not claimed.
- Full Flask route/database integration remains a next verification task on a machine with `requirements.txt` installed.
- Actual mark-entry/attendance workflows, university-result PDF parsing/linking, official calculation snapshots, promotion policies, and full reporting remain future phases. The UI must not claim those workflows are already functional.

Do not connect to the old database or migrate real records until the dedicated migration phase has a reviewed mapping, dry run and verified backup/restore rehearsal.


## Current increment: Student management + term enrollment

Implemented: programme registry, student profile create/edit, PRN pending and explicit verification, term-specific enrollment create, basic regular/DSE semester rules, curriculum-version/programme consistency checks, write permissions, and audit events. See `PHASE_3_STUDENT_ENROLLMENT.md` for route scope and acceptance tests. Do not treat a missing curriculum assignment as approval to enter marks; the UI explicitly warns that subject-level operations must wait for an approved curriculum.


## Current increment: syllabus-to-curriculum

See `PHASE_2_SYLLABUS_CURRICULUM.md` and `PHASE_2_SYLLABUS_FLOW.png`. It uses the existing curriculum-version and subject tables, so this increment adds routes/services/templates but no database columns. A new source file creates a draft; every extracted suggestion remains review-required; assessment components and credits must be confirmed before submission; approval and activation are separate; prior active versions are retired and retained.
