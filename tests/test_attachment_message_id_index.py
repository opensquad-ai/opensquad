"""Attachment.message_id must be indexed for per-message attachment lookups."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.models import Attachment  # noqa: E402


def test_attachment_message_id_column_indexed():
    assert Attachment.__table__.c.message_id.index is True


def test_ensure_indexes_includes_attachments_message_id():
    src = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend" / "app" / "database.py"
    text = src.read_text(encoding="utf-8")
    assert "ix_attachments_message_id" in text
    assert "ON attachments (message_id)" in text
