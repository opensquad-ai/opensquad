# -*- coding: utf-8 -*-
"""Tests for structured_log — trace IDs and JSON formatting."""
import json
import logging

import pytest

from opensquad.structured_log import (
    TraceContext,
    get_trace_id,
    new_trace_id,
    JSONFormatter,
    TraceFormatter,
    get_logger,
    configure_structured_logging,
)


class TestTraceContext:
    def test_get_trace_id_returns_none_by_default(self):
        assert get_trace_id() is None

    def test_trace_context_sets_id(self):
        with TraceContext() as tid:
            assert get_trace_id() == tid
            assert len(tid) == 16

    def test_trace_context_custom_id(self):
        with TraceContext(trace_id="my_custom_id") as tid:
            assert get_trace_id() == "my_custom_id"

    def test_trace_context_restores_after_exit(self):
        original = get_trace_id()
        with TraceContext(trace_id="temp"):
            assert get_trace_id() == "temp"
        assert get_trace_id() == original


class TestNewTraceId:
    def test_is_hex_string(self):
        tid = new_trace_id()
        assert len(tid) == 16
        int(tid, 16)  # should not raise

    def test_is_unique(self):
        ids = {new_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestJSONFormatter:
    def test_basic_json_output(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["msg"] == "hello world"
        assert "ts" in data

    def test_includes_trace_id(self):
        formatter = JSONFormatter()
        with TraceContext(trace_id="trace_abc"):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="with trace", args=(), exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data["trace_id"] == "trace_abc"

    def test_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="extra", args=(), exc_info=None,
        )
        record.agent_id = "agent_1"
        record.turn = 5
        output = formatter.format(record)
        data = json.loads(output)
        assert data["agent_id"] == "agent_1"
        assert data["turn"] == 5


class TestTraceFormatter:
    def test_plain_format(self):
        formatter = TraceFormatter(
            "%(name)s - %(levelname)s - %(message)s%(trace_id_str)s"
        )
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="plain msg", args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "plain msg" in output
        assert "trace_id" not in output

    def test_includes_trace_id_text(self):
        formatter = TraceFormatter(
            "%(name)s - %(levelname)s - %(message)s%(trace_id_str)s"
        )
        with TraceContext(trace_id="tid_xyz"):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="traced msg", args=(), exc_info=None,
            )
            output = formatter.format(record)
            assert "traced msg" in output
            assert "trace_id=tid_xyz" in output


class TestGetLogger:
    def test_returns_adapter(self):
        logger = get_logger("my_module")
        assert isinstance(logger, logging.LoggerAdapter)

    def test_adapter_injects_trace_id(self, caplog):
        with TraceContext(trace_id="injected_123"):
            logger = get_logger("test_logger")
            with caplog.at_level(logging.INFO, logger="test_logger"):
                logger.info("test message")
        assert "test message" in caplog.text
        # The adapter sets extra.trace_id on the record
        record = caplog.records[0]
        assert getattr(record, "trace_id", None) == "injected_123"


class TestConfigureStructuredLogging:
    def test_json_mode(self, capsys):
        configure_structured_logging(json_mode=True, level="INFO")
        root = logging.getLogger()
        root.info("json test")
        captured = capsys.readouterr()
        line = captured.out.strip()
        data = json.loads(line)
        assert data["msg"] == "json test"
        assert data["level"] == "INFO"

    def test_plain_mode(self, capsys):
        configure_structured_logging(json_mode=False, level="INFO")
        root = logging.getLogger()
        with TraceContext(trace_id="plain_tid"):
            root.info("plain test")
        captured = capsys.readouterr()
        assert "plain test" in captured.out
        assert "trace_id=plain_tid" in captured.out
