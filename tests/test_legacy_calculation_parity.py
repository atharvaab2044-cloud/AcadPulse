"""Regression tests for calculations carried over from the previous repository.

Loads the service package under an isolated namespace so these pure calculation
tests can run before Flask dependencies are installed. Full application tests
still import the real `app` package normally.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "app" / "services"


def _load_legacy_services():
    package_name = "acadpulse_test_services"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(SERVICE_DIR)]
        sys.modules[package_name] = package
    loaded = {}
    for short_name in ("academic_policy", "legacy_calculations", "legacy_assessment_summary", "legacy_mark_service"):
        qualified = f"{package_name}.{short_name}"
        if qualified in sys.modules:
            loaded[short_name] = sys.modules[qualified]
            continue
        spec = spec_from_file_location(qualified, SERVICE_DIR / f"{short_name}.py")
        module = module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        loaded[short_name] = module
    return loaded


modules = _load_legacy_services()
calc = modules["legacy_calculations"]
service = modules["legacy_mark_service"]
summary = modules["legacy_assessment_summary"]


class LegacyCalculationParityTests(unittest.TestCase):
    def test_grade_table_representative_points(self):
        expected = {
            95: ("EX", Decimal("10.0")),
            90: ("AA", Decimal("9.0")),
            85: ("AB", Decimal("8.5")),
            80: ("BB", Decimal("8.0")),
            75: ("BC", Decimal("7.5")),
            70: ("CC", Decimal("7.0")),
            65: ("CD", Decimal("6.5")),
            60: ("DD", Decimal("6.0")),
            55: ("DE", Decimal("5.5")),
            50: ("EE", Decimal("5.0")),
            39: ("FF", Decimal("0.0")),
        }
        for marks, pair in expected.items():
            with self.subTest(marks=marks):
                self.assertEqual(calc.get_grade(marks), pair)

    def test_theory_requires_external_minimum_and_total_minimum(self):
        fails_external = calc.compute_final_result(31, 19, 4)
        self.assertFalse(fails_external["is_passed"])
        self.assertEqual(fails_external["grade"], "FF")
        self.assertEqual(fails_external["credits_earned"], 0)
        self.assertIn("ESE below minimum", fails_external["fail_reason"])

        fails_total = calc.compute_final_result(19, 20, 4)
        self.assertFalse(fails_total["is_passed"])
        self.assertEqual(fails_total["total"], 39.0)
        self.assertIn("Total below 40", fails_total["fail_reason"])

        passes = calc.compute_final_result(25, 20, 4)
        self.assertTrue(passes["is_passed"])
        self.assertEqual(passes["total"], 45.0)
        self.assertEqual(passes["grade"], "EE")
        self.assertEqual(passes["credits_earned"], 4)

    def test_theory_internal_uses_best_two_of_ct1_ct2_assignment_plus_midsem(self):
        from types import SimpleNamespace
        row = SimpleNamespace(ct1=8, ct2=9, assignment=7, midsem=16,
                              best_ct=None, ca_marks=None, internal_total=None)
        calc.update_internal_totals(row)
        self.assertEqual(row.best_ct, 9.0)
        self.assertEqual(row.ca_marks, 17.0)
        self.assertEqual(row.internal_total, 33.0)

    def test_theory_missing_component_stays_pending(self):
        from types import SimpleNamespace
        row = SimpleNamespace(ct1=8, ct2=None, assignment=7, midsem=16,
                              best_ct=0, ca_marks=0, internal_total=0)
        calc.update_internal_totals(row)
        self.assertIsNone(row.best_ct)
        self.assertIsNone(row.ca_marks)
        self.assertIsNone(row.internal_total)
        self.assertFalse(calc.is_theory_internal_complete(row))

    def test_lab_formula_and_threshold(self):
        result = calc.compute_lab_result(25, 24, 35, 2)
        self.assertEqual(result["internal"], 49.0)
        self.assertEqual(result["total"], 84.0)
        self.assertEqual(result["grade"], "AB")
        self.assertTrue(result["is_passed"])
        self.assertEqual(result["credits_earned"], 2)

        fail = calc.compute_lab_result(10, 10, 19, 2)
        self.assertEqual(fail["total"], 39.0)
        self.assertFalse(fail["is_passed"])
        self.assertEqual(fail["grade"], "FF")

    def test_sgpa_weights_credits_and_failed_subjects(self):
        sgpa, registered, earned = calc.calculate_sgpa([
            {"credits_registered": 4, "grade_point": 8, "is_passed": True},
            {"credits_registered": 2, "grade_point": 0, "is_passed": False},
        ])
        self.assertEqual(sgpa, Decimal("5.33"))
        self.assertEqual(registered, 6)
        self.assertEqual(earned, 4)

    def test_cgpa_is_credit_weighted(self):
        cgpa = calc.calculate_cgpa([
            {"credits_registered": 20, "sgpa": "8.00"},
            {"credits_registered": 10, "sgpa": "6.00"},
        ])
        self.assertEqual(cgpa, Decimal("7.33"))

    def test_incomplete_sgpa_behavior_is_preserved_in_pure_helper(self):
        sgpa, registered, earned = calc.calculate_sgpa([
            {"credits_registered": 3, "grade_point": 8, "is_passed": True},
            {"credits_registered": 4, "grade_point": None, "is_passed": None},
        ])
        self.assertEqual(sgpa, Decimal("8.00"))
        self.assertEqual(registered, 3)
        self.assertEqual(earned, 3)

    def test_legacy_fractional_grade_gap_is_explicitly_preserved(self):
        # Source behavior: integer-labelled bands can leave decimal totals unmatched.
        self.assertEqual(calc.get_grade(85.5), ("FF", Decimal("0.0")))

    def test_service_preview_uses_source_formula(self):
        result = service.calculate_theory(
            ct1=8, ct2=9, assignment=7, midsem=16, external=20, credits=4,
        )
        self.assertEqual(result["internal_total"], 33.0)
        self.assertEqual(result["total"], 53.0)
        self.assertEqual(result["grade"], "DE")
        self.assertTrue(result["is_passed"])
        self.assertEqual(result["calculation_authority"], "provisional-preview")
        self.assertEqual(result["formula_profile"], service.LEGACY_PROFILE_ID)

    def test_service_missing_marks_is_pending_not_zero(self):
        result = service.calculate_theory(
            ct1=8, ct2=None, assignment=7, midsem=16, external=30, credits=4,
        )
        self.assertIsNone(result["internal_total"])
        self.assertIsNone(result["total"])
        self.assertIsNone(result["is_passed"])

    def test_service_validates_mark_ranges(self):
        with self.assertRaisesRegex(ValueError, "CT1 must be between 0 and 10"):
            service.calculate_theory(
                ct1=11, ct2=9, assignment=7, midsem=16, external=30, credits=4,
            )
        with self.assertRaisesRegex(ValueError, "Theory external must be 0–60"):
            service.calculate_theory(
                ct1=8, ct2=9, assignment=7, midsem=16, external=61, credits=4,
            )

    def test_lab_legacy_internal_fallback_is_explicit_and_credit_safe(self):
        result = service.calculate_lab(
            ca1=None, ca2=None, external=20, credits=2, legacy_internal=25,
        )
        self.assertEqual(result["total"], 45)
        self.assertTrue(result["is_passed"])
        self.assertEqual(result["credits_earned"], 2)
        self.assertIn("final_result_utils.py", result["source_reference"])

    def test_authoritative_calculation_requires_active_mapped_policy(self):
        with self.assertRaises(service.PolicyPendingError):
            service.calculate_theory(
                ct1=8, ct2=9, assignment=7, midsem=16, external=30, credits=4,
                authoritative=True,
            )
        policy = {
            "status": "active", "policy_type": "grading",
            "rules_json": {"calculation_profile": service.LEGACY_PROFILE_ID},
        }
        result = service.calculate_theory(
            ct1=8, ct2=9, assignment=7, midsem=16, external=30, credits=4,
            policy=policy, authoritative=True,
        )
        self.assertEqual(result["calculation_authority"], "official-policy")

    def test_legacy_adapter_requires_integer_credits(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            service.calculate_theory(
                ct1=8, ct2=9, assignment=7, midsem=16, external=30, credits=3.5,
            )

    def test_previous_assessment_summary_thresholds_are_preserved(self):
        self.assertEqual(summary.PASS_THRESHOLD, {
            "CT1": 4, "CT2": 4, "MIDSEM": 8, "ASSIGNMENT": 4,
        })
        self.assertEqual(summary.classify_summary_mark("CT1", 4), "passed")
        self.assertEqual(summary.classify_summary_mark("MIDSEM", 7), "failed")
        self.assertEqual(summary.classify_summary_mark("ASSIGNMENT", None), "absent")
        self.assertEqual(summary.summary_column("unknown"), "ct1")

    def test_subject_filter_excludes_parent_audit_and_nonpositive_credits(self):
        from types import SimpleNamespace
        self.assertFalse(calc.is_gradable_subject(SimpleNamespace(is_audit=True, credits=4, is_active=True)))
        self.assertFalse(calc.is_gradable_subject(SimpleNamespace(is_elective=True, parent_subject_code=None, credits=3, is_active=True)))
        self.assertFalse(calc.is_gradable_subject(SimpleNamespace(credits=0, is_active=True)))
        self.assertTrue(calc.is_gradable_subject(SimpleNamespace(is_elective=True, parent_subject_code="EL_PARENT", credits=3, is_active=True)))


if __name__ == "__main__":
    unittest.main()
