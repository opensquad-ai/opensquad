"""
OpenSquad Feishu Bot Adapter (Multi-bot)

Runs multiple Feishu bot instances using subprocess isolation.
Each bot has its own app_id/app_secret and is bound to a specific Agent.

Config in system_config.json:
  "feishu": {
    "bots": [
      {"name": "coder", "app_id": "...", "app_secret": "...", "agent_id": "coder-001"},
      {"name": "pm",    "app_id": "...", "app_secret": "...", "agent_id": "pm-001"}
    ]
  }

Why subprocesses instead of threads:
  The lark-oapi SDK uses a *module-level* global asyncio event loop
  (``lark_oapi.ws.client.loop``) shared by all Client instances.
  ``Client.start()`` calls ``loop.run_until_complete()`` which blocks forever.
  A second Client in another thread hits the same global loop and raises
  "This event loop is already running".  Since the SDK references the global
  ``loop`` variable throughout its lifetime (reconnect, ping, message handling),
  monkey-patching it per-thread is unsafe.  Subprocess isolation gives each bot
  its own Python interpreter and its own event loop -- clean and reliable.

Usage:
  python -m plugins.feishu.adapter
  python -m plugins.feishu.adapter --single <bot_index>   (internal, used by subprocess)
  scripts/start_feishu.bat
"""

import datetime
import json
import logging
import os
import subprocess
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

import requests

# Add project root to path.
# In frozen mode, APPEND (not insert(0)): the Agent Python's site-packages
# must win over _internal/ loose copies of third-party packages, whose
# transitive deps (e.g. click) live only in the PYZ archive and would crash
# with ModuleNotFoundError. See external_api/adapter.py for full rationale.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    if getattr(sys, "frozen", False):
        sys.path.append(ROOT_DIR)
    else:
        sys.path.insert(0, ROOT_DIR)

import contextlib

from plugins.feishu.config import (
    EXTERNAL_ADAPTER_URL,
    EXTERNAL_API_KEY,
    FEISHU_LOG_LEVEL,
    FeishuBotConfig,
    bot_config_from_env,
    bot_config_to_json,
    is_service_enabled,
    load_bot_configs,
    load_bot_configs_fresh,
)

# Debug: write config info to file for diagnosis
# In frozen mode the plugin source dir is read-only (Program Files), so diag
# logs must go to the writable workspace logs dir, not next to __file__.
# Use the self-contained _service_runtime helper (no opensquad import — the
# Agent Python that runs this service in frozen mode does not have opensquad).
try:
    from plugins._service_runtime import config_path as _rt_config_path
    from plugins._service_runtime import get_workspace as _rt_get_workspace
    from plugins._service_runtime import workspace_logs_dir as _rt_workspace_logs_dir

    _FEISHU_LOG_DIR = _rt_workspace_logs_dir("feishu")
    os.makedirs(_FEISHU_LOG_DIR, exist_ok=True)
except Exception:
    # Fallback: use temp dir (NOT __file__ dir — in frozen mode that's the
    # read-only _internal/plugins/feishu/ and makedirs/open would fail).
    import tempfile as _tempfile_feishu

    _FEISHU_LOG_DIR = os.path.join(_tempfile_feishu.gettempdir(), "opensquad_feishu")
    os.makedirs(_FEISHU_LOG_DIR, exist_ok=True)


def _feishu_diag_path() -> str:
    """Return the writable feishu_diag.log path (workspace logs dir)."""
    return os.path.join(_FEISHU_LOG_DIR, "feishu_diag.log")


try:
    _dbg_path = os.path.join(_FEISHU_LOG_DIR, "debug_config.txt")
    _WORKSPACE_ROOT = _rt_get_workspace()
    _CONFIG_PATH = _rt_config_path()

    with open(_dbg_path, "w", encoding="utf-8") as _f:
        _f.write(f"OPENSQUAD_WORKSPACE={os.environ.get('OPENSQUAD_WORKSPACE', 'NOT SET')}\n")
        _f.write(f"EXTERNAL_API_KEY={EXTERNAL_API_KEY!r}\n")
        _f.write(f"EXTERNAL_ADAPTER_URL={EXTERNAL_ADAPTER_URL!r}\n")
        _f.write(f"_WORKSPACE_ROOT={_WORKSPACE_ROOT!r}\n")
        _f.write(f"_CONFIG_PATH={_CONFIG_PATH!r}\n")
except Exception as _e:
    print(f"[DEBUG ERROR] {_e}")

# ── Logging ──
logging.basicConfig(
    level=getattr(logging, FEISHU_LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("feishu_adapter")

# ══════════════════════════════════════════════
#  Feishu SDK imports
# ══════════════════════════════════════════════

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImChatAccessEventBotP2pChatEnteredV1,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
        ReplyMessageResponse,
    )
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.ws import Client as FeishuWSClient
except ImportError:
    logger.error("Missing lark-oapi SDK. Install with: pip install lark-oapi")
    sys.exit(1)


# ══════════════════════════════════════════════
#  Single Bot Runner
# ══════════════════════════════════════════════


class FeishuBotRunner:
    """
    Manages one Feishu bot instance.
    Runs its own lark REST client + WebSocket event listener.
    """

    def __init__(self, cfg: FeishuBotConfig):
        self.cfg = cfg
        self.lark_client: lark.Client = None
        self.bot_open_id: str = ""
        self._log = logging.getLogger(f"feishu.{cfg.name}")
        self._chat_name_cache: dict = {}  # chat_id -> chat name
        # Status tracking (P1.4: surfaced to launcher -> Web UI)
        self.message_count: int = 0
        self.error_count: int = 0
        self.last_error: str = ""
        self.last_message_at: str = ""
        self.last_error_at: str = ""

    def init_client(self):
        """Initialize Feishu REST API client."""
        self.lark_client = (
            lark.Client.builder()
            .app_id(self.cfg.app_id)
            .app_secret(self.cfg.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._log.info(f"[{self.cfg.name}] REST client initialized")

    def fetch_bot_info(self):
        """Fetch bot's own open_id for @mention detection."""
        try:
            token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            token_resp = requests.post(
                token_url,
                json={
                    "app_id": self.cfg.app_id,
                    "app_secret": self.cfg.app_secret,
                },
                timeout=10,
            )
            token_data = token_resp.json()
            if token_data.get("code") != 0:
                self._log.warning(f"Failed to get tenant_access_token: {token_data}")
                return

            access_token = token_data["tenant_access_token"]
            info_url = "https://open.feishu.cn/open-apis/bot/v3/info/"
            info_resp = requests.get(
                info_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                timeout=10,
            )
            info_data = info_resp.json()
            if info_data.get("code") == 0:
                self.bot_open_id = info_data.get("bot", {}).get("open_id", "")
                bot_name = info_data.get("bot", {}).get("app_name", "unknown")
                self._log.info(f"[{self.cfg.name}] Bot: {bot_name}, open_id={self.bot_open_id}")
            else:
                self._log.warning(f"Failed to get bot info: {info_data}")
        except Exception as e:
            self._log.warning(f"Bot info fetch error (non-fatal): {e}")

    def on_message_receive(self, data: P2ImMessageReceiveV1) -> None:
        """Feishu message receive callback."""
        # Write ALL received events to diag log for debugging
        try:
            diag_path = _feishu_diag_path()
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(diag_path, "a", encoding="utf-8") as f:
                f.write(
                    f"[{ts}] RECEIVED: type={type(data).__name__}, has_event={bool(data.event)}, has_msg={bool(data.event and data.event.message)}\n"
                )
        except Exception:
            pass
        try:
            event = data.event
            if not event or not event.message:
                return

            message = event.message
            sender = event.sender

            # Skip non-user messages
            if not sender or sender.sender_type != "user":
                return

            sender_open_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id or ""
            message_id = message.message_id or ""
            chat_type = message.chat_type or ""
            msg_type = message.message_type or ""

            # Extract sender name from mentions or sender_id
            sender_name = self._get_sender_name(sender, message.mentions)

            self._log.info(
                f"[{self.cfg.name}] Message: type={chat_type}, msg_type={msg_type}, "
                f"chat={chat_id}, sender={sender_name or sender_open_id}"
            )

            # Text only
            if msg_type != "text":
                self._reply(message_id, "Only text messages are supported currently.")
                return

            # Extract text
            text = self._extract_text(message.content, message.mentions)
            if not text:
                return

            # Group chat: check @bot
            if chat_type == "group" and not self._is_mentioned_bot(message.mentions):
                self._log.debug(f"[{self.cfg.name}] Group msg without @bot, skip")
                return

            # Get group name (cached)
            chat_name = ""
            if chat_type == "group" and chat_id:
                chat_name = self._get_chat_name(chat_id)

            self._log.info(f'[{self.cfg.name}] Processing: "{text[:60]}{"..." if len(text) > 60 else ""}"')

            # ── Status tracking (P1.4) ──
            self.message_count += 1
            self.last_message_at = datetime.datetime.now().isoformat(timespec="seconds")

            # Process in thread to avoid blocking SDK event loop
            thread = threading.Thread(
                target=self._process_and_reply,
                args=(message_id, chat_id, sender_open_id, text, chat_type, sender_name, chat_name, chat_id),
                daemon=True,
            )
            thread.start()

        except Exception as e:
            self._log.error(f"[{self.cfg.name}] Message handler error: {e}", exc_info=True)

    def _get_sender_name(self, sender, mentions) -> str:
        """Try to extract sender display name from event data."""
        # Try sender_id.name (available in some SDK versions)
        try:
            name = getattr(sender.sender_id, "name", None)
            if name:
                return name
        except Exception:
            pass
        # Fallback: find the first non-bot mention name (heuristic for group chats)
        # Not reliable, so return empty
        return ""

    def _get_chat_name(self, chat_id: str) -> str:
        """Fetch chat/group name via Feishu API (cached)."""
        if chat_id in self._chat_name_cache:
            return self._chat_name_cache[chat_id]
        try:
            if not self.lark_client:
                return ""
            from lark_oapi.api.im.v1 import GetChatRequest

            req = GetChatRequest.builder().chat_id(chat_id).build()
            resp = self.lark_client.im.v1.chat.get(req)
            if resp.success() and resp.data and resp.data.name:
                name = resp.data.name
                self._chat_name_cache[chat_id] = name
                self._log.info(f"[{self.cfg.name}] Chat name: {chat_id} -> {name}")
                return name
        except Exception as e:
            self._log.debug(f"Failed to fetch chat name: {e}")
        self._chat_name_cache[chat_id] = ""
        return ""

    def _extract_text(self, content: str, mentions=None) -> str:
        if not content:
            return ""
        try:
            content_dict = json.loads(content)
            text = content_dict.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = str(content)

        import re

        text = re.sub(r"@_user_\d+", "", text).strip()
        return text

    def _is_mentioned_bot(self, mentions) -> bool:
        if not mentions:
            return False
        for mention in mentions:
            user_id_obj = getattr(mention, "id", None)
            if user_id_obj:
                open_id = getattr(user_id_obj, "open_id", None)
                if open_id and self.bot_open_id and open_id == self.bot_open_id:
                    return True
        # Fallback: if bot_open_id unknown but mentions exist, assume @bot
        return bool(not self.bot_open_id and mentions)

    def _process_and_reply(
        self,
        message_id: str,
        chat_id: str,
        sender_id: str,
        text: str,
        chat_type: str,
        sender_name: str = "",
        chat_name: str = "",
        source_chat_id: str = "",
    ):
        """Call External Adapter and reply (runs in worker thread)."""
        diag_log_path = _feishu_diag_path()

        def _diag(msg: str):
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(diag_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {msg}\n")
            print(f"[Feishu] {msg}", flush=True)

        try:
            url = f"{EXTERNAL_ADAPTER_URL}/api/chat"
            headers = {"Content-Type": "application/json"}
            if EXTERNAL_API_KEY:
                headers["X-API-Key"] = EXTERNAL_API_KEY

            is_group = chat_type == "group"
            channel = "feishu_group" if is_group else "feishu_private"

            payload = {
                "agent_id": self.cfg.agent_id,
                "message": text,
                "user_id": f"feishu_{sender_id}",
                "timeout": self.cfg.request_timeout,
                "channel": channel,
                "sender_name": sender_name,
                "chat_name": chat_name,
                "source_chat_id": source_chat_id,
            }

            _diag("=== Request to External API ===")
            _diag(f"URL: {url}")
            _diag(f"API Key: {EXTERNAL_API_KEY[:12]}...{EXTERNAL_API_KEY[-4:] if len(EXTERNAL_API_KEY) > 4 else ''}")
            _diag(f"Payload: {json.dumps(payload, ensure_ascii=False)[:500]}")
            _diag(f"Timeout: {self.cfg.request_timeout + 10}s")

            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.cfg.request_timeout + 10,
            )

            _diag(f"Response: status={resp.status_code} body={resp.text[:500]}")

            if resp.status_code == 200:
                reply_text = resp.json().get("message", "")
                if reply_text:
                    self._reply(message_id, reply_text)
                else:
                    self._reply(message_id, "Agent did not return a valid reply.")
            else:
                detail = ""
                with contextlib.suppress(Exception):
                    detail = resp.json().get("detail", "")
                self._log.error(f"Adapter error: {resp.status_code}, detail={detail}")
                _diag(f"ERROR: status={resp.status_code}, detail={detail}")
                self._record_error(f"HTTP {resp.status_code}: {detail[:100]}" if detail else f"HTTP {resp.status_code}")
                if detail:
                    self._reply(message_id, f"Processing failed (error {resp.status_code}): {detail}")
                else:
                    self._reply(message_id, f"Processing failed (error {resp.status_code}).")

        except requests.Timeout:
            self._log.error("Adapter request timed out")
            _diag("EXCEPTION: Timeout")
            self._record_error("Timeout")
            self._reply(message_id, "Agent processing timed out.")
        except requests.ConnectionError as ce:
            self._log.error(f"Cannot connect to adapter: {ce}")
            _diag(f"EXCEPTION: ConnectionError - {ce}")
            self._record_error(f"ConnectionError: {ce}")
            self._reply(message_id, "Agent service unavailable.")
        except Exception as e:
            self._log.error(f"Adapter call error: {e}", exc_info=True)
            _diag(f"EXCEPTION: {type(e).__name__} - {e}")
            self._record_error(f"{type(e).__name__}: {e}")
            self._reply(message_id, "Internal error.")

    def _record_error(self, msg: str):
        """Record an error for status reporting (P1.4)."""
        self.error_count += 1
        self.last_error = msg[:200]
        self.last_error_at = datetime.datetime.now().isoformat(timespec="seconds")

    def _reply(self, message_id: str, text: str):
        """Reply to a Feishu message via REST API."""
        if not self.lark_client:
            self._log.error("Lark client not initialized")
            return
        try:
            content = json.dumps({"text": text}, ensure_ascii=False)
            request = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(ReplyMessageRequestBody.builder().content(content).msg_type("text").build())
                .build()
            )
            response: ReplyMessageResponse = self.lark_client.im.v1.message.reply(request)
            if not response.success():
                self._log.error(f"Reply failed: code={response.code}, msg={response.msg}")
        except Exception as e:
            self._log.error(f"Reply error: {e}", exc_info=True)

    def on_bot_entered(self, data: P2ImChatAccessEventBotP2pChatEnteredV1) -> None:
        self._log.info(f"[{self.cfg.name}] User opened P2P chat")

    def build_ws_client(self) -> FeishuWSClient:
        """Build the WebSocket event client for this bot."""
        event_handler = (
            EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self.on_message_receive).build()
        )

        ws_client = FeishuWSClient(
            self.cfg.app_id,
            self.cfg.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.DEBUG,
        )
        return ws_client


# ══════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════


def _validate_bot_configs(bot_configs: list) -> bool:
    """Check for placeholder/template values that indicate incomplete config."""
    placeholders = {
        "your_app_id_here",
        "your_app_secret_here",
        "your-agent-id",
        "cli_xxxxxxxxxxxxxxxxxxxx",
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "app_id",
        "app_secret",
        "agent_id",
        "your_app_id",
        "your_app_secret",
        "replace_me",
        "TODO",
        "changeme",
    }
    ok = True
    for cfg in bot_configs:
        if cfg.app_id.lower() in {p.lower() for p in placeholders}:
            logger.error(
                f"Bot [{cfg.name}] app_id looks like a placeholder: '{cfg.app_id}'. "
                f"Update system_config.json -> feishu.bots with real credentials."
            )
            ok = False
        if cfg.app_secret.lower() in {p.lower() for p in placeholders}:
            logger.error(
                f"Bot [{cfg.name}] app_secret looks like a placeholder. "
                f"Update system_config.json -> feishu.bots with real credentials."
            )
            ok = False
        if not cfg.agent_id or cfg.agent_id in placeholders:
            logger.error(
                f"Bot [{cfg.name}] agent_id is invalid: '{cfg.agent_id}'. "
                f"Set a real agent ID (create an agent in Web UI first)."
            )
            ok = False
    return ok


def _run_single_bot(bot_index: int = -1, cfg: FeishuBotConfig = None):
    """Run a single bot (called in subprocess or directly for single-bot configs).

    Each subprocess has its own Python interpreter and its own asyncio event
    loop, so the lark-oapi SDK's global ``loop`` variable is not shared.

    Bot config is sourced from (in priority order):
      1. ``cfg`` argument (in-process single-bot mode)
      2. ``FEISHU_BOT_CONFIG_JSON`` env var (subprocess mode set by orchestrator)
      3. ``load_bot_configs()[bot_index]`` (legacy subprocess mode with --single N)
    """
    # ── Startup diagnostic: write to file immediately ──
    diag_path = _feishu_diag_path()

    def _diag(msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(diag_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
        # Also write to stdout so messages appear in launcher's PIPE log (containers, nohup, etc.)
        print(f"[Feishu] {msg}", flush=True)

    _diag("=== Feishu Adapter STARTING ===")
    _diag(f"PID: {os.getpid()}")
    _diag(f"Python: {sys.executable}")
    _diag(f"OPENSQUAD_WORKSPACE: {os.environ.get('OPENSQUAD_WORKSPACE', 'NOT SET')}")
    _diag(f"CWD: {os.getcwd()}")

    # Check service enabled in subprocess too (config may have changed)
    if not is_service_enabled():
        logger.info("Feishu service is disabled (services.feishu.enabled=false). Exiting.")
        sys.exit(0)

    # Resolve bot config
    if cfg is None:
        cfg = bot_config_from_env()
    if cfg is None and bot_index >= 0:
        bot_configs = load_bot_configs()
        if bot_index < 0 or bot_index >= len(bot_configs):
            logger.error(f"Invalid bot index: {bot_index} (have {len(bot_configs)} bots)")
            sys.exit(1)
        cfg = bot_configs[bot_index]
    if cfg is None:
        logger.error("No bot config provided (neither cfg arg, FEISHU_BOT_CONFIG_JSON env, nor valid --single index).")
        sys.exit(1)

    # Validate this specific bot's config
    if not _validate_bot_configs([cfg]):
        logger.error(f"Bot [{cfg.name}] has invalid config, exiting.")
        sys.exit(1)

    runner = FeishuBotRunner(cfg)

    _diag(f"Bot config: name={cfg.name}, app_id='{cfg.app_id}', agent={cfg.agent_id}")
    _diag(f"app_id eq cli_a91be2a381381bef: {cfg.app_id == 'cli_a91be2a381381bef'}")
    _diag(f"app_secret len: {len(cfg.app_secret)}")

    # Force CWD to the project root (where standalone tests work).
    # ROOT_DIR = src/, but the correct working directory is the project root
    # (one level above src). The standalone test that works uses this CWD.
    project_root = os.path.dirname(ROOT_DIR)  # src/ -> project root
    os.chdir(project_root)
    _diag(f"Bot config: name={cfg.name}, app_id={cfg.app_id[:10]}...{cfg.app_id[-4:]}, agent={cfg.agent_id}")
    _diag(f"Changed CWD to: {os.getcwd()}")

    # Build WS client first (prevents SDK event loop conflict), then REST client for replies.
    ws_client = runner.build_ws_client()
    runner.init_client()
    bot_log = runner._log

    # Start per-bot sidecar status writer (P1.4)
    _sidecar_stop = threading.Event()
    _sidecar_thread = threading.Thread(
        target=_bot_sidecar_status_loop,
        args=(runner, _sidecar_stop),
        daemon=True,
        name=f"status-{cfg.name}",
    )
    _sidecar_thread.start()

    backoff = 5  # initial reconnect delay in seconds
    try:
        while True:
            try:
                bot_log.info(f"[{runner.cfg.name}] Connecting to Feishu WebSocket...")
                ws_client.start()  # blocks until disconnected
                # Normal exit (clean disconnect) — reset backoff
                backoff = 5
            except KeyboardInterrupt:
                bot_log.info(f"[{runner.cfg.name}] Interrupted, stopping")
                break
            except Exception as e:
                err_str = f"WebSocket: {type(e).__name__}: {e}"[:200]
                runner._record_error(err_str)
                bot_log.error(f"[{runner.cfg.name}] WebSocket error: {e}, reconnecting in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
    finally:
        _sidecar_stop.set()


def _pipe_subprocess_output(proc: subprocess.Popen, bot_name: str):
    """Read subprocess stdout/stderr and forward to main process logger."""
    try:
        for line in iter(proc.stdout.readline, ""):
            if line:
                # Print directly so log format from subprocess is preserved
                print(line, end="", flush=True)
    except Exception:
        pass


def main():
    # ── Check if service is enabled ──
    if not is_service_enabled():
        logger.info("Feishu service is disabled (services.feishu.enabled=false). Exiting.")
        sys.exit(0)

    # ── Check for --single mode (subprocess entry) ──
    if "--single" in sys.argv:
        idx_pos = sys.argv.index("--single") + 1
        if idx_pos >= len(sys.argv):
            logger.error("--single requires a bot index argument")
            sys.exit(1)
        bot_index = int(sys.argv[idx_pos])
        _run_single_bot(bot_index=bot_index)
        return

    # ── Main process: orchestrator ──
    print("=" * 60)
    print("  OpenSquad Feishu Adapter (Orchestrator)")
    print("=" * 60)
    print(f"  Adapter URL:  {EXTERNAL_ADAPTER_URL}")
    print(f"  Workspace:    {os.environ.get('OPENSQUAD_WORKSPACE', 'NOT SET')}")
    print("=" * 60)

    # Initial load
    bot_configs = load_bot_configs()
    if not bot_configs:
        logger.warning("No enabled Feishu bots configured yet.")
        logger.warning("Add bot entries in Web UI Plugin Manager, or edit system_config.json -> feishu.bots")
        logger.warning("Watching for config changes (Ctrl+C to exit)...")
    else:
        if not _validate_bot_configs(bot_configs):
            logger.error("Fix the config issues above. Watching for config changes anyway...")

    # bot_processes: app_id -> BotProcess
    bot_processes: dict = {}
    if os.path.isfile(_CONFIG_PATH):
        with contextlib.suppress(Exception):
            os.path.getmtime(_CONFIG_PATH)

    # Initial spawn
    for cfg in bot_configs:
        bp = _start_bot(cfg)
        if bp:
            bot_processes[cfg.app_id] = bp

    stop_event = threading.Event()

    # ── Start config watcher thread ──
    watcher = threading.Thread(
        target=_config_watcher_loop,
        args=(bot_processes, stop_event),
        daemon=True,
        name="feishu-config-watcher",
    )
    watcher.start()

    # ── Start status writer thread (P1.4) ──
    status_writer = threading.Thread(
        target=_status_writer_loop,
        args=(bot_processes, stop_event),
        daemon=True,
        name="feishu-status-writer",
    )
    status_writer.start()

    logger.info(f"Orchestrator running. {len(bot_processes)} bot(s) active. Ctrl+C to stop.")

    # ── Main loop: monitor subprocess liveness, restart on crash ──
    try:
        while not stop_event.is_set():
            time.sleep(2)
            for app_id, bp in list(bot_processes.items()):
                if bp.proc and bp.proc.poll() is None:
                    # Healthy — reset backoff if it has been alive > 60s
                    if bp.last_restart_time > 0 and time.time() - bp.last_restart_time > 60:
                        bp.restart_delay = 5
                    continue

                # Process died — restart with backoff
                bp.restart_count += 1
                delay = bp.restart_delay
                logger.warning(
                    f"Bot [{bp.cfg.name}] (app_id={app_id[:8]}...) "
                    f"subprocess exited (code {bp.proc.returncode if bp.proc else 'N/A'}), "
                    f"restarting in {delay}s (attempt {bp.restart_count})..."
                )
                # Don't busy-loop if the bot keeps dying
                stop_event.wait(delay)
                if stop_event.is_set():
                    break
                bp.restart_delay = min(bp.restart_delay * 2, 120)

                # Try to restart with same config (watcher will fix config if it changed)
                new_proc = _spawn_bot_subprocess(bp.cfg)
                if new_proc:
                    bp.proc = new_proc
                    bp.last_restart_time = time.time()
                    # Restart pipe thread
                    if bp.pipe_thread and bp.pipe_thread.is_alive():
                        pass  # old thread will die on its own when old proc is None
                    bp.pipe_thread = threading.Thread(
                        target=_pipe_subprocess_output,
                        args=(new_proc, bp.cfg.name),
                        daemon=True,
                        name=f"pipe-{bp.cfg.name}",
                    )
                    bp.pipe_thread.start()
                    logger.info(f"  Restarted [{bp.cfg.name}] (PID {new_proc.pid})")

    except KeyboardInterrupt:
        logger.info("Shutting down Feishu adapter orchestrator...")
    finally:
        stop_event.set()
        for app_id, bp in list(bot_processes.items()):
            _stop_bot(bp, timeout=3)
        logger.info("Orchestrator stopped.")


# ══════════════════════════════════════════════
#  Orchestrator: per-bot process management
# ══════════════════════════════════════════════


# Per-bot sidecar status file path. The orchestrator reads these to
# aggregate per-bot stats into its own status.json. The naming uses
# app_id (sanitized) to keep the file name filesystem-safe.
def _bot_sidecar_path(app_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in app_id)
    # Use _rt_get_workspace() (self-contained, no opensquad import) instead of
    # os.getcwd() — in frozen mode cwd = read-only _internal/, which would
    # make makedirs/open fail with PermissionError.
    try:
        ws = _rt_get_workspace()
    except Exception:
        ws = os.environ.get("OPENSQUAD_WORKSPACE") or ""
    if not ws:
        import tempfile

        ws = os.path.join(tempfile.gettempdir(), "opensquad")
    base_dir = os.path.join(ws, "data", "plugins", "feishu", "bot_status")
    return os.path.join(base_dir, f"{safe}.json")


def _bot_sidecar_status_loop(runner: "FeishuBotRunner", stop_event: threading.Event):
    """Write this bot's stats to a sidecar file every 2s.

    The orchestrator (main process) reads these files to aggregate
    message_count, error_count, last_error, last_message_at per bot
    into its status.json.
    """
    path = _bot_sidecar_path(runner.cfg.app_id)
    with contextlib.suppress(Exception):
        os.makedirs(os.path.dirname(path), exist_ok=True)

    while not stop_event.is_set():
        stop_event.wait(2.0)
        try:
            payload = {
                "app_id": runner.cfg.app_id,
                "name": runner.cfg.name,
                "agent_id": runner.cfg.agent_id,
                "pid": os.getpid(),
                "message_count": runner.message_count,
                "error_count": runner.error_count,
                "last_error": runner.last_error,
                "last_error_at": runner.last_error_at,
                "last_message_at": runner.last_message_at,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass


class BotProcess:
    """Tracks a single bot subprocess and its stats."""

    __slots__ = (
        "cfg",
        "error_count",
        "last_error",
        "last_message_at",
        "last_restart_time",
        "message_count",
        "pipe_thread",
        "proc",
        "restart_count",
        "restart_delay",
        "started_at",
    )

    def __init__(self, cfg: FeishuBotConfig):
        self.cfg = cfg
        self.proc = None
        self.pipe_thread = None
        self.restart_count = 0
        self.last_restart_time = 0.0
        self.restart_delay = 5
        self.started_at = ""
        # Stats from subprocess via status file (P1.4)
        self.message_count = 0
        self.error_count = 0
        self.last_error = ""
        self.last_message_at = ""

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def to_status(self) -> dict:
        return {
            "app_id": self.cfg.app_id,
            "name": self.cfg.name,
            "agent_id": self.cfg.agent_id,
            "pid": self.proc.pid if self.is_alive() else None,
            "alive": self.is_alive(),
            "started_at": self.started_at,
            "restart_count": self.restart_count,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_at": "",
            "last_message_at": self.last_message_at,
        }


def _spawn_bot_subprocess(cfg: FeishuBotConfig) -> subprocess.Popen | None:
    """Spawn a subprocess running a single Feishu bot.

    The bot config is passed via the FEISHU_BOT_CONFIG_JSON env var so the
    subprocess does not need to re-read system_config.json.

    NOTE: We run the adapter by *file path* (``sys.executable adapter.py``)
    rather than ``-m plugins.feishu.adapter`` because the Agent Python embed
    uses a ``python311._pth`` file which makes Python **ignore PYTHONPATH**.
    With ``-m``, the child cannot find the ``plugins`` package (it lives in
    the frozen ``_internal/`` dir, which is only reachable via PYTHONPATH).
    Running by path lets the script's own ``sys.path.insert(0, ROOT_DIR)``
    set up imports correctly.
    """
    try:
        env = os.environ.copy()
        env["FEISHU_BOT_CONFIG_JSON"] = bot_config_to_json(cfg)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        adapter_path = os.path.join(ROOT_DIR, "plugins", "feishu", "adapter.py")
        proc = subprocess.Popen(
            [sys.executable, adapter_path, "--single", "-1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(ROOT_DIR),  # project root, not src/
            env=env,
        )
        return proc
    except Exception as e:
        logger.error(f"Failed to spawn bot [{cfg.name}]: {e}")
        return None


def _start_bot(cfg: FeishuBotConfig) -> BotProcess | None:
    """Validate config, spawn subprocess, register BotProcess."""
    if not _validate_bot_configs([cfg]):
        logger.error(f"Bot [{cfg.name}] has invalid config, skipping.")
        return None
    bp = BotProcess(cfg)
    proc = _spawn_bot_subprocess(cfg)
    if proc is None:
        return None
    bp.proc = proc
    bp.started_at = datetime.datetime.now().isoformat(timespec="seconds")
    bp.last_restart_time = time.time()
    bp.pipe_thread = threading.Thread(
        target=_pipe_subprocess_output,
        args=(proc, cfg.name),
        daemon=True,
        name=f"pipe-{cfg.name}",
    )
    bp.pipe_thread.start()
    logger.info(f"  Started [{cfg.name}] (app_id={cfg.app_id[:8]}..., agent={cfg.agent_id}, PID={proc.pid})")
    return bp


def _stop_bot(bp: BotProcess, timeout: int = 5):
    """Terminate a bot's subprocess (if alive)."""
    if bp.proc is None:
        return
    if bp.proc.poll() is not None:
        return  # already dead
    try:
        bp.proc.terminate()
        bp.proc.wait(timeout=timeout)
        logger.info(f"  Stopped [{bp.cfg.name}] (PID {bp.proc.pid})")
    except subprocess.TimeoutExpired:
        try:
            bp.proc.kill()
            logger.warning(f"  Force-killed [{bp.cfg.name}] (PID {bp.proc.pid})")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"  Error stopping [{bp.cfg.name}]: {e}")


def _config_needs_restart(old: FeishuBotConfig, new: FeishuBotConfig) -> bool:
    """Return True if any field that requires reconnect has changed."""
    # name change is cosmetic, no restart needed
    if old.app_id != new.app_id:
        return True  # different app (shouldn't happen — keyed by app_id)
    if old.app_secret != new.app_secret:
        return True
    if old.agent_id != new.agent_id:
        return True
    if old.encrypt_key != new.encrypt_key:
        return True
    return old.verification_token != new.verification_token


def _apply_bot_diff(old_bots: dict, new_bots: dict, bot_processes: dict):
    """Compare old vs new bot dicts (app_id -> FeishuBotConfig) and apply diff."""
    old_ids = set(old_bots.keys())
    new_ids = set(new_bots.keys())

    # 1) Removed bots — stop subprocess
    for app_id in old_ids - new_ids:
        cfg = old_bots[app_id]
        if app_id in bot_processes:
            logger.info(f"[diff] Bot removed: {cfg.name} ({app_id[:8]}...)")
            _stop_bot(bot_processes[app_id])
            del bot_processes[app_id]

    # 2) New bots — start subprocess
    for app_id in new_ids - old_ids:
        cfg = new_bots[app_id]
        logger.info(f"[diff] Bot added: {cfg.name} ({app_id[:8]}...)")
        bp = _start_bot(cfg)
        if bp:
            bot_processes[app_id] = bp

    # 3) Existing bots — check if reconnect needed
    for app_id in old_ids & new_ids:
        old_cfg = old_bots[app_id]
        new_cfg = new_bots[app_id]
        if _config_needs_restart(old_cfg, new_cfg):
            logger.info(f"[diff] Bot config changed: {new_cfg.name} ({app_id[:8]}...), reconnecting...")
            if app_id in bot_processes:
                _stop_bot(bot_processes[app_id])
            bp = _start_bot(new_cfg)
            if bp:
                bot_processes[app_id] = bp


def _config_watcher_loop(bot_processes: dict, stop_event: threading.Event):
    """Poll system_config.json mtime, apply diff on change.

    Runs in a daemon thread. Polls every 1s.
    """
    last_mtime = 0.0
    if os.path.isfile(_CONFIG_PATH):
        with contextlib.suppress(Exception):
            last_mtime = os.path.getmtime(_CONFIG_PATH)

    # Cache current bot dicts (app_id -> FeishuBotConfig) for diff
    current_bots: dict = {b.cfg.app_id: b.cfg for b in bot_processes.values() if b.cfg}

    while not stop_event.is_set():
        stop_event.wait(1.0)
        try:
            if not os.path.isfile(_CONFIG_PATH):
                continue
            mtime = os.path.getmtime(_CONFIG_PATH)
            if mtime == last_mtime:
                continue
            last_mtime = mtime
            logger.info(f"[watcher] system_config.json changed (mtime={mtime}), reloading bots...")

            new_configs = load_bot_configs_fresh()
            new_bots = {c.app_id: c for c in new_configs}
            _apply_bot_diff(current_bots, new_bots, bot_processes)
            current_bots = new_bots
        except Exception as e:
            logger.error(f"[watcher] error: {e}")


# ══════════════════════════════════════════════
#  Status writer (P1.4)
# ══════════════════════════════════════════════

# Use _rt_get_workspace() for the writable workspace root (self-contained,
# no opensquad import). os.getcwd() fallback would resolve to the read-only
# _internal/ dir in frozen mode and cause PermissionError on status writes.
try:
    _STATUS_WS = _rt_get_workspace()
except Exception:
    _STATUS_WS = os.environ.get("OPENSQUAD_WORKSPACE") or ""
if not _STATUS_WS:
    import tempfile as _tempfile_status

    _STATUS_WS = os.path.join(_tempfile_status.gettempdir(), "opensquad")
_STATUS_PATH = os.path.join(_STATUS_WS, "data", "plugins", "feishu", "status.json")


def _status_writer_loop(bot_processes: dict, stop_event: threading.Event):
    """Periodically write feishu status to status.json for launcher to read.

    The launcher reads this file in PluginServiceProcess.get_status() and
    surfaces it to the Web UI (PluginManagerPage -> ServiceStatusCard).

    Per-bot stats (message_count, error_count, last_error) are read from
    sidecar files written by each bot subprocess (see _bot_sidecar_status_loop).
    """
    while not stop_event.is_set():
        stop_event.wait(2.0)
        try:
            bots_payload = {}
            for app_id, bp in bot_processes.items():
                status = bp.to_status()
                # Merge per-bot stats from sidecar file
                sidecar = _bot_sidecar_path(app_id)
                if os.path.isfile(sidecar):
                    try:
                        with open(sidecar, encoding="utf-8") as f:
                            stats = json.load(f)
                        # Only update stats fields, not pid/alive (those are
                        # the orchestrator's view of the subprocess)
                        status["message_count"] = stats.get("message_count", 0)
                        status["error_count"] = stats.get("error_count", 0)
                        status["last_error"] = stats.get("last_error", "")
                        status["last_error_at"] = stats.get("last_error_at", "")
                        status["last_message_at"] = stats.get("last_message_at", "")
                    except Exception:
                        pass
                bots_payload[app_id] = status

            payload = {
                "schema_version": 1,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "orchestrator_pid": os.getpid(),
                "bots": bots_payload,
            }
            os.makedirs(os.path.dirname(_STATUS_PATH), exist_ok=True)
            tmp = _STATUS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _STATUS_PATH)
        except Exception as e:
            logger.debug(f"[status-writer] {e}")


if __name__ == "__main__":
    main()
