# AcadPulse — Current MVP Scope and Limitations

## What is operationally represented in this codebase

| Workspace | Current implementation | Important validation boundary |
|---|---|---|
| Authentication and roles | Login/logout, explicit admin creation, role guards, CSRF, account password hashing | Deployment hardening, password reset and operational account administration need review |
| Students | Stable internal ID/student code, basic profile, separately pending/verified PRN, audit | Internal ID must not be exposed as a mutable roll number |
| Enrollment | Programme, academic year, semester, curriculum version, term roll/division/batch, admission type/status, regular and DSE checks | Academic progression does not automatically promote the student |
| Programme/scheme | Programme registry and scheme registry | NEP/MIS must be treated as one scheme identity |
| Syllabus/curriculum | Text-based PDF upload and source hash, subject suggestions, draft editing, version diff, submit/review/approve/activate | The extractor is heuristic. OCR/scanned-PDF support and broad layout coverage are not implemented; all suggestions need review |
| Electives | Draft groups/options, student selections and explicit approval | Institutional elective constraints must be confirmed and tested against local policy |
| Coursework | Assessment setup, roster mark entry/correction, lock/publish, subject summary, source-audited CSV/XLSX preview and commit | Legacy formulas support only configured, matching component maxima; mismatched component maxima produce warnings |
| Attendance | Session creation, roster marking/correction, recorded attendance reporting, CSV/XLSX preview/commit, CSV export | Attendance policy/eligibility thresholds are not assumed to be official |
| University result import | PDF storage/hash, generic text candidate extraction, staged edit/review/reject, explicit student/subject/enrollment linking, attempt history | Not a universal parser or a certified DBATU parser. Numeric tokens are not automatically mapped to marks; review is mandatory |
| Effective attempt selection | Admin action with reason and an active, applicable supplementary policy | The selection rule is explicit; no latest/highest-attempt assumption is built in |
| Calculation snapshots | SGPA preview/snapshot, admin approval, CGPA sourced from approved SGPA snapshots, promotion evaluation/snapshot | Official output requires approved, correctly scoped policy; promotion remains pending without policy |
| Analytics/reports | Descriptive overview with CSV/XLSX/PDF exports | Historical fail attempts in exports are descriptive, not a final official backlog calculation |
| Audit | Action/entity/actor with before/after metadata for sensitive workflows | Audit log and retention/backup controls still require production operations review |

## Required data-safety invariants

1. Do not overwrite a student's permanent identity when roll number or class assignment changes.
2. Keep original values and examination attempts; a correction creates traceable before/after metadata.
3. Keep PDF import staging separate from student/exam records until an authorized user explicitly links reviewed rows.
4. Keep syllabus extraction in draft until every required field and assessment definition is reviewed and the version is approved and activated.
5. Do not infer a grade from unknown columns in a result PDF or silently resolve a name-only match.
6. Do not create an official SGPA/CGPA/promotion decision without the applicable approved, versioned rule. Keep missing/ambiguous rules pending.
7. Do not run `init-db` on any existing/shared database; use reviewed migrations only for future schema evolution.
8. Do not point this fresh project at the legacy application's database or automatically import existing student records.

## Known technical limitations before real deployment

- The local build environment could not install Flask packages due to package-index/network unavailability. Dependency-free tests and syntax/template checks run here; the full Flask/SQLAlchemy integration suite must run on Windows after requirements are installed.
- No OCR is implemented. A scanned syllabus or result PDF may show no detected rows and require manual entry/review.
- The generic result extractor suggests subject-code-like lines and preserves raw source evidence, but it is intentionally conservative and does not determine what each numeric token means.
- Legacy grade boundaries have fractional gaps exactly as in the old code. Do not fix/round them without a versioned decision and regression tests.
- The old pure SGPA helper and ORM updater differed in how incomplete subjects affect SGPA. The new adapter and policy gates document that issue; resolve it through approved policy before treating calculation snapshots as official.
- The project has not been independently security-tested or load-tested. Use synthetic test records until permissions, backups, restoration, deployment secret configuration, HTTPS and institution-specific policy approvals are reviewed.
