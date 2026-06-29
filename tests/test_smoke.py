# -*- coding: utf-8 -*-
"""Smoke tests — verify basic imports and version."""
import importlib

import pytest


def test_import_opensquad():
    mod = importlib.import_module("opensquad")
    assert hasattr(mod, "__version__")


def test_version_format():
    """``opensquad.__version__`` must be a well-formed PEP 440 version.

    Once the package started using dev / post / rc / local markers
    (e.g. ``0.3.0.dev0``, ``0.2.0.post0``), the legacy "exactly 3 numeric
    dot-separated parts" check stopped being the right invariant. The
    canonical PEP 440 parser is ``packaging.version.Version`` — using
    it makes the test match what every other tool in the Python
    ecosystem accepts.
    """
    pytest.importorskip("packaging")  # skip if packaging is not installed

    from packaging.version import Version

    mod = importlib.import_module("opensquad")
    # If this parses, the string is a legal PEP 440 version. That is the
    # only invariant this test now asserts.
    Version(mod.__version__)


def test_import_runner():
    mod = importlib.import_module("opensquad.runner")
    assert hasattr(mod, "AgentRunner")


def test_import_parser():
    mod = importlib.import_module("opensquad.parser")
    assert hasattr(mod, "ResponseParser")
