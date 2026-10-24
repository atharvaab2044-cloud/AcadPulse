# Phase 2 — Syllabus-to-Curriculum Workflow

## Goal

An official syllabus PDF is source evidence. It may suggest a curriculum draft, but uploading or extracting a PDF must never change the active curriculum automatically. This pipeline is separate from university-result parsing; only low-level PDF tooling may be shared.

## Workflow

1. Administrator selects an active programme, cohort year and registered or new scheme.
2. Administrator uploads a PDF. The application validates the PDF signature and page count, uses a generated private storage name, computes SHA-256, and checks for the same PDF in the same programme/scheme/cohort scope.
3. PyMuPDF extracts selectable text by page. The first parser suggests subject code, title, semester and explicit credit labels when it can identify them. It carries the source page, an excerpt, and parser confidence to the draft.
4. Every suggested row starts `review_required`. Assessment maxima are not inferred from arbitrary numbers. The reviewer checks the PDF and enters a structured assessment JSON definition or a reason why assessment marks do not apply.
5. Administrator corrects the draft, marks each subject verified/rejected, adds missing rows manually, and removes false-positive suggestions. An uncertain semester is never invented; candidate rows without a semester are skipped and reported for manual addition.
6. Draft submission is blocked until every subject is verified, credit values are present, course type and semester are valid, and each subject has valid assessment components or a clear `not_applicable_reason`.
7. Submission changes status `draft` → `review` and locks its content. Approval is a separate action requiring an audit note (`review` → `approved`). Activation is another separate action (`approved` → `active`).
8. Activating a new version retires an existing active version only for the same programme, scheme and effective cohort year. The retired version, source PDF and subjects are preserved.
9. Re-uploaded new source files create new versions. The comparison shows added, changed, removed and unchanged subjects without changing the prior version.

## Current routes

| Method | Route | Access | Purpose |
|---|---|---|---|
| GET | `/curriculum` | Authenticated | Curriculum registry and upload form |
| POST | `/curriculum/upload` | Admin | Validate/store/hash/extract to draft |
| GET | `/curriculum/<version_id>` | Authenticated | Subject review, version metadata, readiness and diff |
| GET | `/curriculum/<version_id>/source` | Authenticated | Download original syllabus PDF from private storage |
| GET/POST | `/curriculum/<version_id>/subjects/new` | Admin | Add a missing subject manually |
| GET/POST | `/curriculum/<version_id>/subjects/<subject_id>/edit` | Admin | Review/correct a draft subject |
| POST | `/curriculum/<version_id>/subjects/<subject_id>/delete` | Admin | Remove a false-positive suggestion from a draft only |
| POST | `/curriculum/<version_id>/submit-review` | Admin | Submit a ready draft for approval |
| POST | `/curriculum/<version_id>/approve` | Admin | Approve with a required audit note |
| POST | `/curriculum/<version_id>/activate` | Admin | Activate approved version, retiring prior active version in scope |
| POST | `/curriculum/<version_id>/reject` | Admin | Reject a submitted version with a required reason |

All state-changing forms are CSRF-protected. Write routes are administrator-only. The existing student enrollment module accepts only approved/active curriculum versions, so a draft is never offered as a usable curriculum.

## Main fields used

### `CurriculumVersion`

- `version_code`: stable human-readable version code, unique.
- `program_id`: academic programme this version applies to.
- `scheme_id`: curriculum scheme; NEP/MIS uses one code, `NEP_MIS`.
- `effective_cohort_year`: cohort applicability; independent of a DSE student's actual admission year.
- `status`: draft/review/approved/active/retired/rejected workflow state.
- `source_filename`: sanitized display name only, not a file path.
- `source_sha256`: duplicate-file and integrity key.
- `source_storage_key`: generated relative key inside private uploads; never derived from the uploaded file name.
- `extracted_at`, `submitted_for_review_at`, `approved_by_id`, `approved_at`, `approval_note`: workflow evidence.

### `CurriculumSubject`

- `subject_code`, `title`, `semester_number`, `credits`, `course_type`: normalized curriculum fields.
- `assessment_structure`: JSON object such as `{"components":[{"name":"Internal","max_marks":40},{"name":"External","max_marks":60}],"total_max":100}`. Add `minimum_marks` only when supported by the syllabus. For a genuinely unassessed subject, use `{"components":[],"not_applicable_reason":"Source-backed explanation of why assessment is not applicable."}`.
- `source_page`, `extraction_confidence`: page reference and parser suggestion confidence, not correctness certification.
- `review_status`: `review_required`, `verified`, or `rejected`.
- `notes`: reviewer context. The actual original PDF is preserved separately.

No new database table or column is required for this increment; it uses existing `curriculum_versions`, `curriculum_subjects`, `curriculum_schemes` and `audit_events` tables.

## Parser behavior and limitations

- This increment uses selectable text and basic line-pattern extraction. It is a conservative candidate generator, not a universal syllabus parser.
- Scanned/low-text PDFs are retained as drafts and flagged. OCR is not run by this version; users may add subject rows manually. An OCR stage can be added in a later increment with review evidence.
- Unknown, ambiguous or unassigned-semester lines are not forced into a guessed semester. They are skipped and reported. A page that looks like a table of contents because it contains multiple semester labels is treated as ambiguous and flagged for manual entry.
- Credit labels like `Credits: 4` and `(3 Credits)` are suggested. Other layouts may leave credits blank or only weakly inferred; a person must confirm them.
- Assessment components/maxima are not guessed from arbitrary numbers; they require review in structured JSON.
- Extracted confidence is a ranking hint, never an approval decision.
- The hash identifies exact file content only. A different PDF hash does not imply a meaningful curriculum change; the human-readable revision diff is still required.

## Acceptance criteria

- Unauthenticated users cannot upload or change curriculum state.
- Invalid, oversized, encrypted, or wrong-type files create no curriculum draft.
- Valid text PDFs create a draft with private source storage and SHA-256; every extracted subject is `review_required`.
- Scanned or unrecognized layouts can be retained for manual entry without activating subjects.
- Duplicate same-scope PDF content does not create duplicate versions.
- A draft cannot be submitted until all subjects are verified, credits are confirmed, and assessment structure is valid or explicitly not applicable.
- A submitted/approved/active/retired version is read-only through this workflow.
- Approval and activation are separate explicit actions with audit events.
- Activation retires, but does not delete, the previous active version in the matching programme/scheme/cohort scope.
- Revision comparison reports added, changed, removed and unchanged subject rows while keeping both versions untouched.
- Tests cover parser suggestions, ambiguous semester handling, duplicate rows/files, validation, role checks, lifecycle transitions, diff output and previous-version retention.

## Local setup

PyMuPDF is included in `requirements.txt` for this increment. In the existing `.venv`, run:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python run.py
```

Do not run `init-db` against an existing populated database just to activate this module. It adds no schema fields. The existing active curriculum is unchanged until an authorized administrator runs activation on a reviewed version.
