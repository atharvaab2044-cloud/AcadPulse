"""Policy guards shared by calculation and progression services.

This module intentionally contains no university-specific grade boundaries or
promotion thresholds. Those values must come from an approved, effective policy.
"""


class PolicyPendingError(RuntimeError):
    """Raised when a result cannot be treated as authoritative without policy."""


def _has_configured_value(value):
    """An empty object/list is not a configured rule set."""
    if value is None or value is False or value == "":
        return False
    if isinstance(value, dict):
        return any(_has_configured_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_configured_value(item) for item in value)
    return True


def require_active_policy(policy, purpose: str):
    """Return an active policy or fail closed.

    `policy` may be an AcademicPolicy model or a mapping used by unit tests.
    An approved-but-not-active draft is not sufficient for live calculations.
    """
    if policy is None:
        raise PolicyPendingError(f"No active academic policy is configured for {purpose}.")
    status = policy.get("status") if isinstance(policy, dict) else getattr(policy, "status", None)
    policy_type = policy.get("policy_type") if isinstance(policy, dict) else getattr(policy, "policy_type", None)
    rules = policy.get("rules_json") if isinstance(policy, dict) else getattr(policy, "rules_json", None)
    if status != "active":
        raise PolicyPendingError(f"The academic policy for {purpose} is not active (status: {status or 'missing'}).")
    if not isinstance(rules, dict) or not _has_configured_value(rules):
        raise PolicyPendingError(f"The active {policy_type or 'academic'} policy has no configured rules for {purpose}.")
    return policy
