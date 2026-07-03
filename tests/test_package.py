"""Smoke tests: the package skeleton is importable and versioned."""

import importlib

import quickdraw


def test_version() -> None:
    assert quickdraw.__version__


def test_subpackages_importable() -> None:
    for name in ("data", "training", "serving", "monitoring"):
        assert importlib.import_module(f"quickdraw.{name}")
