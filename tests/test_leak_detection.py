"""
测试泄漏检测函数是否正确处理系统标签

从 runner.py 直接导入真实方法，消除代码复制（原为 MockRunner 内嵌完整副本）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Only needed when running this file standalone on a GBK Windows console.
# Under pytest, stdout is a capture wrapper — replacing it here breaks pytest's
# capture at shutdown ("I/O operation on closed file") because the wrapper is
# garbage-collected and closes its buffer mid-run.
if sys.platform == "win32" and "pytest" not in sys.modules:
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from unittest.mock import MagicMock

from opensquad.runner import AgentRunner


def _make_runner():
    r = MagicMock(spec=AgentRunner)
    r._is_leaked_tool_params = AgentRunner._is_leaked_tool_params.__get__(r, AgentRunner)
    return r


def test_system_tags_not_leaked():
    runner = _make_runner()
    assert not runner._is_leaked_tool_params("<title>查询天气</title>")
    assert not runner._is_leaked_tool_params("<thought>思考中</thought>")
    xml = '<tool_call name="system.echo"><message>hello</message></tool_call>'
    assert not runner._is_leaked_tool_params(xml)


def test_json_leak_detected():
    runner = _make_runner()
    assert runner._is_leaked_tool_params("{}")
    assert runner._is_leaked_tool_params('{"key": "value"}')


def test_plain_text_not_leaked():
    runner = _make_runner()
    assert not runner._is_leaked_tool_params("")
    assert not runner._is_leaked_tool_params("Hello, how can I help?")
