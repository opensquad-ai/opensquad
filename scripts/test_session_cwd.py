"""Quick test: verify _check_session_cwd reads .session_cwd and applies cwd."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["OPENSQUAD_WORKSPACE"] = r"C:\ai_work\pro0\opensquad_runtime_deploy"

from opensquad._context import AgentContext, set_current_context
from opensquad.input_hub import InputHub
from opensquad.utils.path_utils import get_workspace_root

AGENT_DIR = r"C:\ai_work\pro0\opensquad_runtime_deploy\agents\agent301"
CWD_FILE = os.path.join(AGENT_DIR, ".session_cwd")
EXPECTED = r"C:\Users\adminuser\Desktop\game2"

print("=== session_cwd test ===")
print(f"agent_dir: {AGENT_DIR}")
print(f"cwd_file exists: {os.path.isfile(CWD_FILE)}")
if os.path.isfile(CWD_FILE):
    print(f"cwd_file content: {open(CWD_FILE, encoding='utf-8').read()}")

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

print(f"before: get_workspace_root() = {get_workspace_root()}")
print(f"before: ctx.session_cwd = {ctx.session_cwd!r}")

hub._check_session_cwd()

print(f"after:  get_workspace_root() = {get_workspace_root()}")
print(f"after:  ctx.session_cwd = {ctx.session_cwd!r}")

ok = os.path.normcase(get_workspace_root()) == os.path.normcase(EXPECTED)
print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
