# Legacy mark calculation compatibility profile

**Profile ID:** `dbatu_ece_legacy_calculations_v1`  
**Source:** owner-provided `Student-analysis-system-/calculations.py`, plus result-wrapper behavior in `final_result_utils.py`.  
**Purpose:** preserve behavior during the rebuild and make every future change deliberate and testable.

This is a source-derived description of the previous repository, not an independent certification of current DBATU regulations. AcadPulse must distinguish the compatibility profile from faculty-approved official policy. For authoritative results, the active applicable `AcademicPolicy.rules_json` must explicitly contain `{"calculation_profile":"dbatu_ece_legacy_calculations_v1"}` and the policy must be approved/active.

## 1. Theory internal calculation

Inputs and maxima:

| Component | Maximum | Role |
|---|---:|---|
| CT1 | 10 | Candidate continuous-assessment component |
| CT2 | 10 | Candidate continuous-assessment component |
| Assignment | 10 | Candidate continuous-assessment component |
| Mid-semester (`midsem`) | 20 | Added after continuous assessment |

Formula copied from `update_internal_totals()`:

1. Require **all four components** before calculating. Missing one means pending; it is not replaced with zero.
2. Select the highest two marks from CT1, CT2 and Assignment.
3. `CA = highest_component_1 + highest_component_2` (out of 20).
4. `Internal Total = CA + Midsem` (out of 40).
5. Legacy output fields map to `best_ct` = highest component, `ca_marks` = best-two sum, `internal_total` = CA + midsem.

## 2. Theory final result

- Internal maximum: 40.
- External/ESE maximum: 60.
- `total = internal + external`, out of 100.
- A theory result passes only when **both** `external >= 20` and `total >= 40`.
- Failure of either requirement gives grade `FF`, grade point `0`, and `credits_earned = 0`.
- Missing internal or external values produce an incomplete/pending result in the legacy result wrapper.
- A pass earns registered subject credits; the previous routine did not apportion partial earned credits for a failed subject.

## 3. Lab/project result

Preferred current formula:

- CA1: /30.
- CA2: /30.
- `internal = CA1 + CA2` (out of 60).
- External: /40.
- `total = internal + external` (out of 100).
- Pass when `total >= 40`; unlike theory, `compute_lab_result()` has no independent external minimum check.
- When both CA components are unavailable but an old record has `internal` and `external`, `final_result_utils.py` retains a backward-compatibility path using those values directly; AcadPulse keeps this only as an explicit legacy-data compatibility path.
- Missing required values yield pending values rather than a forced zero.

## 4. Grade table

`get_grade(total)` uses the following ranges and grade points:

| Total mark range | Grade | Grade point |
|---:|---|---:|
| 91–100 | EX | 10.0 |
| 86–90 | AA | 9.0 |
| 81–85 | AB | 8.5 |
| 76–80 | BB | 8.0 |
| 71–75 | BC | 7.5 |
| 66–70 | CC | 7.0 |
| 61–65 | CD | 6.5 |
| 56–60 | DD | 6.0 |
| 51–55 | DE | 5.5 |
| 40–50 | EE | 5.0 |
| 0–39 | FF | 0.0 |

The code applies these integer-labelled boundaries to numeric values using `low <= total <= high`; therefore, some fractional totals between the integer ranges match no band and fall through to `FF`. This is a legacy edge case. Do not repair it silently: confirm whether fractional totals are valid and what rounding should apply, then introduce a versioned policy/test change if instructed.

## 5. Subjects eligible for SGPA/CGPA

`is_gradable_subject()` excludes:
- audit subjects;
- attendance-only subjects;
- inactive subjects;
- subjects with invalid or nonpositive credits;
- elective parent/container rows (`is_elective=True` and no `parent_subject_code`).

Actual elective option rows with a parent code can be counted if otherwise gradable. The selected subject/enrollment still needs to be validated by the new data model before calculation.

## 6. SGPA and earned credits

Pure helper `calculate_sgpa(subject_results)`:

- Ignore results with missing grade point or `is_passed is None`.
- Skip invalid/nonpositive credits.
- Include completed failed subjects in registered credits, with grade point 0.
- `SGPA = sum(credits × grade_point) / sum(registered credits)`; round to 2 decimal places with `Decimal` `ROUND_HALF_UP`.
- `credits_earned` sums subject credits only where `is_passed` is true.
- Return `(None, 0, 0)` when no completed eligible credits remain.

**Important legacy inconsistency:** the legacy ORM updater `update_sgpa_cgpa_for_student()` sets the whole semester SGPA to `None` if **any** gradable enrolled subject is missing required marks; this differs from the pure helper's behavior of skipping incomplete rows. The rebuild documents and tests these as two different legacy code paths. Before connecting the new coursework/result workflows, decide which behavior is the intended rule and version that decision; do not quietly mix them.

## 7. CGPA and history

- The pure `calculate_cgpa()` computes a credits-registered-weighted average of semester SGPA values and returns `None` if an SGPA is missing or a semester's registered credits are invalid.
- The updater stores cumulative history progressively: Semester 3's CGPA should be through Semester 3, Semester 4's through Semester 4, etc., and it blocks current/later CGPA after a pending earlier semester.
- The updater orders rows by `semester_id` only, although the table is keyed by PRN + semester + academic year. The new schema must use a defined chronological term order and a validated effective-result selection rule when the CGPA adapter is implemented.
- The correct treatment of supplementary/revaluation attempts and whether older SGPA/CGPA snapshots are recalculated is controlled by future approved policy; old attempts must remain stored.

## 8. CT/assessment summaries are separate

`summary_utils.py` defines descriptive internal-assessment thresholds: CT1=4, CT2=4, Midsem=8 and Assignment=4 for class summary reporting. These constants and classification behavior are kept in `app/services/legacy_assessment_summary.py`. These are report thresholds from the source project; they are not the same as the final theory pass rule and must not be reused as ESE/final-result policy without confirmation.

## 9. Versioning and migration rules

- `app/services/legacy_calculations.py` keeps source behavior for parity/regression testing.
- `app/services/legacy_mark_service.py` adapts inputs to this profile and labels previews as provisional.
- No route should write a result directly by duplicating formulas.
- Any future change must update the profile version, regression fixtures, audit/calculation metadata and the documented reason.
- The old `update_sgpa_cgpa_for_student()` function references the previous repository's ORM fields and is retained as a reference only. Use a new-schema adapter when integrating calculations with AcadPulse models.
