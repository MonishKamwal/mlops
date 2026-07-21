"""Guards the failsafe sweeper's safety invariant: it acts only on tier=ephemeral.

The sweeper deletes real infrastructure, so the one thing that must never regress is its
selection predicate — a bug that returned True for a persistent (or untagged) resource
could delete the wrong thing. Full deletion paths are exercised by the Phase 3 kill-test
against real AWS; here we pin the predicate.
"""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("eks_sweep", Path("scripts/eks_sweep.py"))
eks_sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eks_sweep)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ([{"Key": "tier", "Value": "ephemeral"}], True),
        ([{"Key": "tier", "Value": "persistent"}], False),  # never sweep persistent
        ([{"Key": "project", "Value": "mlops-quickdraw"}], False),  # right project, wrong tier
        ([{"Key": "tier", "Value": "ephemeral"}, {"Key": "x", "Value": "y"}], True),
        ([], False),  # untagged is out of reach
        (None, False),
    ],
)
def test_is_ephemeral(tags: list | None, expected: bool) -> None:
    assert eks_sweep._is_ephemeral(tags) is expected
