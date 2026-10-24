"""Dependency-free unit tests for the academic policy gate."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "services" / "academic_policy.py"
MODULE_NAME = "acadpulse_policy_gate_test_module"
if MODULE_NAME not in sys.modules:
    spec = spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
else:
    module = sys.modules[MODULE_NAME]

PolicyPendingError = module.PolicyPendingError
require_active_policy = module.require_active_policy


def test_missing_policy_fails_closed():
    try:
        require_active_policy(None, "promotion eligibility")
    except PolicyPendingError as exc:
        assert "No active academic policy" in str(exc)
    else:
        raise AssertionError("Missing policy should fail closed")


def test_draft_policy_cannot_drive_live_calculations():
    policy = {"status": "draft", "policy_type": "promotion", "rules_json": {"min_credits": 10}}
    try:
        require_active_policy(policy, "promotion eligibility")
    except PolicyPendingError as exc:
        assert "not active" in str(exc)
    else:
        raise AssertionError("Draft policy should not drive live calculations")


def test_active_policy_with_no_rules_is_not_usable():
    policy = {"status": "active", "policy_type": "grading", "rules_json": {}}
    try:
        require_active_policy(policy, "grade calculation")
    except PolicyPendingError as exc:
        assert "no configured rules" in str(exc)
    else:
        raise AssertionError("Empty policy should not be usable")


def test_active_policy_with_rules_is_usable():
    policy = {"status": "active", "policy_type": "grading", "rules_json": {"grade_boundaries": [{"min": 0, "max": 100}]}}
    assert require_active_policy(policy, "grade calculation") is policy


def test_active_policy_with_empty_rule_list_is_not_usable():
    policy = {"status": "active", "policy_type": "grading", "rules_json": {"grade_boundaries": []}}
    try:
        require_active_policy(policy, "grade calculation")
    except PolicyPendingError as exc:
        assert "no configured rules" in str(exc)
    else:
        raise AssertionError("An empty list must not count as configured policy")
