"""Integration test: push + get_user_response triggers _check_session_cwd."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["OPENSQUAD_WORKSPACE"] = r"C:\ai_work\pro0\opensquad_runtime_deploy"

from opensquad._context import AgentContext, set_current_context
from opensquad.input_hub import InputHub
from opensquad.utils.path_utils import get_workspace_root

AGENT_DIR = r"C:\ai_work\pro0\opensquad_runtime_deploy\agents\agent301"
EXPECTED = r"C:\Users\adminuser\Desktop\game2"


async def main():
    ctx = AgentContext(
        input_hub=None,
        message_queue=None,
        state_manager=None,
        session_manager=None,
        sleep_controller=None,
        event_pipeline=None,
        message_router=None,
        agent_id="agent301-001",
        agent_name="Agent301",
        config_path=os.path.join(AGENT_DIR, "config.json"),
        agent_dir=AGENT_DIR,
    )
    set_current_context(ctx)

    hub = InputHub()
    hub.set_agent_context(AGENT_DIR)
    hub.push("test message", source="gateway")

    data = await hub.get_user_response()
    cwd = get_workspace_root()
    print(f"input source: {data.get('source')}")
    print(f"get_workspace_root(): {cwd}")
    print(f"ctx.session_cwd: {ctx.session_cwd!r}")
    ok = os.path.normcase(cwd) == os.path.normcase(EXPECTED)
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
