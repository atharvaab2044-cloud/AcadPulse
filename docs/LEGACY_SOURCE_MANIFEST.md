# Legacy calculation source manifest

These hashes identify the owner-provided reference files that were inspected while implementing the AcadPulse compatibility profile. Hashes refer to the source files as found in the supplied legacy project snapshot.

| Source file | Purpose carried into AcadPulse | SHA-256 |
|---|---|---|
| `calculations.py` | Grade table, theory final result, lab result, gradable subject filter, SGPA/CGPA helpers, internal component formula, input validation and legacy ORM updater reference | `3243843ee5848c09463dd1b88c6e6304aa40b95dfd7f8dcbc32a7e7dfa4703ab` |
| `final_result_utils.py` | Incomplete/pending behavior and old lab `internal + external` compatibility fallback | `3f21ebdb55173a633db588937f7be52673adeccb057d87b202d6c85ce2d715ca` |
| `summary_utils.py` | Internal assessment summary thresholds for CT1, CT2, Midsem and Assignment | `55fe1f27cce67385420c994530642ff1c58dd2e3075560474b05a9498f364eef` |

The source was inspected from the supplied `Student-analysis-system--main` project snapshot, not inferred from a generic grading scheme. `legacy_calculations.py` contains the prior calculator code, preceded by a provenance/usage header. `legacy_mark_service.py` adapts the formula for AcadPulse and adds safe input-range checks and an official-policy gate; it does not duplicate the arithmetic.

## Current repository reference

The relevant calculation definitions were also reviewed against the repository's current `main` branch:

- [`calculations.py` on GitHub](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/calculations.py) — grade table, theory/lab result, SGPA/CGPA and internal totals.
- [`final_result_utils.py` on GitHub](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/final_result_utils.py) — pending-result wrapper and legacy lab fallback.
- [`summary_utils.py` on GitHub](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/summary_utils.py) — class-summary assessment thresholds.

The current `main` branch was inspected in this build session. The detailed implementation and regression fixtures in this project follow the documented formula behavior. The hashes above are for the supplied local project snapshot, not claimed as checksums of the remote file bytes.
