"""Require the isolated entry point before importing any business configuration."""
import sys
import pytest

if not getattr(sys, "_aicshub_offline_guard", False):
    raise pytest.UsageError(
        "Unsafe direct pytest invocation blocked before business imports. "
        "Use: python scripts/test_offline.py --profile active "
        "(or python scripts/test_offline.py <pytest arguments>). "
        "Real-provider evaluations use their explicit budgeted scripts."
    )
