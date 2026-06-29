# -*- coding: utf-8 -*-
"""
OpenSquad Telegram Bot Adapter (Multi-bot)

Runs multiple Telegram bot instances in a single process.
Each bot has its own token and is bound to a specific Agent.

Config in system_config.json:
  "telegram": {
    "bots": [
      {"name": "main",   "bot_token": "TOKEN_A", "agent_id": "coder-001", "enabled": true},
      {"name": "pm-bot", "bot_token": "TOKEN_B", "agent_id": "pm-001",    "enabled": true}
    ]
  }

Features:
  - One process, multiple bots
  - Each bot -> one agent (fixed binding)
  - Private chat: always respond
  - Group chat: respond only when bot is mentioned or replied to
  - /start, /help show which agent this bot is connected to

Usage:
  python -m plugins.telegram.adapter
  scripts/start_telegram.bat
"""

import asyncio
import logging
import re
import sys
import os

import requests

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from plugins.telegram.config import (
    TelegramBotConfig,
    load_bot_configs,
    is_service_enabled,
    EXTERNAL_ADAPTER_URL,
    EXTERNAL_API_KEY,
    TELEGRAM_LOG_LEVEL,
)

# ── Logging ──
logging.basicConfig(
    level=getattr(logging, TELEGRAM_LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telegram_adapter")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ══════════════════════════════════════════════
#  python-telegram-bot imports
# ══════════════════════════════════════════════

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )
    from telegram.request import HTTPXRequest
    from telegram.error import NetworkError, TimedOut
except ImportError:
    logger.error(
        "Missing python-telegram-bot library. Install with:\n"
        "  pip install python-telegram-bot"
    )
    sys.exit(1)


# ══════════════════════════════════════════════
#  Single Bot Runner
# ══════════════════════════════════════════════

class TelegramBotRunner:
    """
    Manages one Telegram bot instance.
    Each runner has its own Application, bot_username, bot_id, and fixed agent_id.
    """

    def __init__(self, cfg: TelegramBotConfig):
        self.cfg = cfg
        self.bot_username: str = ""
        self.bot_id: int = 0
        self.application: Application = None
        self._log = logging.getLogger(f"tg.{cfg.name}")

    def _build_http_request(self) -> HTTPXRequest:
        """Build httpx request with proxy and timeout from config."""
        kwargs = {
            "connect_timeout": float(self.cfg.connect_timeout),
            "read_timeout": float(self.cfg.request_timeout),
            "write_timeout": float(self.cfg.request_timeout),
            "pool_timeout": 10.0,
        }
        if self.cfg.proxy:
            kwargs["proxy"] = self.cfg.proxy
        return HTTPXRequest(**kwargs)

    async def _initialize_with_retry(
        self,
        max_attempts: int = 3,
        base_delay: float = 2.0,
    ) -> None:
        """Initialize the application with exponential-backoff retries.

        ``Application.initialize()`` triggers a synchronous ``getMe`` call
        to ``api.telegram.org``. On a cold start (DNS cache miss + TCP
        handshake + TLS negotiation) this can exceed the configured
        ``connect_timeout`` on restricted or high-latency links. The
        long-polling loop reuses the established connection once it is
        up, so a single retry almost always succeeds. We try up to
        ``max_attempts`` times with ``base_delay * attempt`` seconds
        between attempts before giving up.
        """
        for attempt in range(1, max_attempts + 1):
            try:
                await self.application.initialize()
                return
            except (TimedOut, NetworkError) as e:
                if attempt >= max_attempts:
                    raise
                delay = base_delay * attempt
                self._log.warning(
                    f"getMe timed out on cold start "
                    f"(attempt {attempt}/{max_attempts}, "
                    f"{type(e).__name__}: {e}). "
                    f"Retrying in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)

    async def start(self):
        """Initialize and start polling (non-blocking)."""
        request = self._build_http_request()
        self.application = (
            Application.builder()
            .token(self.cfg.bot_token)
            .request(request)
            .get_updates_request(request)
            .build()
        )

        # Register handlers
        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("help", self._cmd_help))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        # Initialize bot (get_me) and start polling.
        # _initialize_with_retry handles cold-start network slowness; the
        # outer except is kept so the final failure is logged with proxy
        # guidance and re-raised to run_all_bots.
        try:
            await self._initialize_with_retry()
            bot = self.application.bot
            self.bot_username = bot.username or ""
            self.bot_id = bot.id
            self._log.info(
                f"Bot ready: @{self.bot_username} (id={self.bot_id}) -> agent={self.cfg.agent_id}"
            )
        except (TimedOut, NetworkError) as e:
            proxy_hint = self.cfg.proxy or "(not set)"
            self._log.error(
                f"Cannot reach Telegram API after retries: {e}. "
                f"If you are behind a firewall, configure proxy in "
                f"system_config.json -> telegram.proxy (current: {proxy_hint}). "
                f"Example: \"http://127.0.0.1:7890\""
            )
            raise

        await self.application.start()
        await self.application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message"],
        )
        self._log.info(f"Polling started for @{self.bot_username}")

    async def stop(self):
        """Stop polling and shutdown."""
        if self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                self._log.warning(f"Error during shutdown: {e}")

    # ── Commands ──

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"Hello! I'm OpenSquad bot [{self.cfg.name}].\n"
            f"Connected to agent: {self.cfg.agent_id}\n\n"
            f"Send me a message and I'll forward it to the agent.\n"
            f"In group chats, mention me (@) or reply to my messages."
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"OpenSquad Telegram Bot [{self.cfg.name}]\n\n"
            f"Agent: {self.cfg.agent_id}\n"
            f"Timeout: {self.cfg.request_timeout}s\n\n"
            f"Commands:\n"
            f"  /start - Welcome message\n"
            f"  /help  - This help\n\n"
            f"Private chat: Send any text directly.\n"
            f"Group chat: Mention me (@) or reply to my messages."
        )

    # ── Message handling ──

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        if not message or not message.text:
            return

        chat = message.chat
        user = message.from_user
        chat_type = chat.type

        is_group = chat_type in ("group", "supergroup")

        if is_group and not self._should_respond_in_group(message):
            return

        text = self._extract_text(message.text)
        if not text:
            return

        self._log.info(
            f"[{self.cfg.name}] msg from {user.username or user.id}: "
            f"\"{text[:60]}{'...' if len(text) > 60 else ''}\""
        )

        # Determine channel
        if chat_type == "private":
            channel = "telegram_private"
        elif is_group:
            channel = "telegram_group"
        else:
            channel = "telegram"

        # Collect context info
        sender_name = ""
        if user:
            parts = [user.first_name or "", user.last_name or ""]
            sender_name = " ".join(p for p in parts if p) or user.username or str(user.id)
        chat_name = chat.title or ""  # group name (empty for private chats)

        # Call External Adapter
        reply_text = self._call_adapter(
            chat_id=chat.id,
            user_id=user.id,
            text=text,
            channel=channel,
            sender_name=sender_name,
            chat_name=chat_name,
            source_chat_id=str(chat.id),
        )

        if reply_text:
            for chunk in _split_message(reply_text, max_len=4000):
                await message.reply_text(chunk)
        else:
            await message.reply_text("Agent did not return a valid reply.")

    def _should_respond_in_group(self, message) -> bool:
        if message.reply_to_message:
            reply_user = message.reply_to_message.from_user
            if reply_user and reply_user.id == self.bot_id:
                return True

        if message.entities:
            for entity in message.entities:
                if entity.type == "mention":
                    mentioned = message.text[entity.offset:entity.offset + entity.length]
                    if self.bot_username and mentioned.lower() == f"@{self.bot_username.lower()}":
                        return True
                elif entity.type == "text_mention":
                    if entity.user and entity.user.id == self.bot_id:
                        return True
        return False

    def _extract_text(self, raw_text: str) -> str:
        text = raw_text.strip()
        if self.bot_username:
            text = re.sub(
                rf"@{re.escape(self.bot_username)}\b", "", text, flags=re.IGNORECASE
            ).strip()
        return text

    def _call_adapter(self, chat_id: int, user_id: int, text: str,
                      channel: str, sender_name: str = "", chat_name: str = "",
                      source_chat_id: str = "") -> str:
        try:
            url = f"{EXTERNAL_ADAPTER_URL}/api/chat"
            headers = {"Content-Type": "application/json"}
            if EXTERNAL_API_KEY:
                headers["X-API-Key"] = EXTERNAL_API_KEY

            payload = {
                "agent_id": self.cfg.agent_id,
                "message": text,
                "user_id": f"telegram_{user_id}",
                "timeout": self.cfg.request_timeout,
                "channel": channel,
                "sender_name": sender_name,
                "chat_name": chat_name,
                "source_chat_id": source_chat_id,
            }

            self._log.info(f"-> adapter: agent={self.cfg.agent_id}, channel={channel}")

            resp = requests.post(
                url, json=payload, headers=headers,
                timeout=self.cfg.request_timeout + 10,
            )

            if resp.status_code == 200:
                return resp.json().get("message", "")
            else:
                self._log.error(f"Adapter error: {resp.status_code}")
                return f"Processing failed (error {resp.status_code}), please try again."

        except requests.Timeout:
            self._log.error("Adapter request timed out")
            return "Agent processing timed out."
        except requests.ConnectionError:
            self._log.error("Cannot connect to adapter")
            return "Agent service unavailable."
        except Exception as e:
            self._log.error(f"Adapter call error: {e}", exc_info=True)
            return "Internal error."


# ══════════════════════════════════════════════
#  Utilities
# ══════════════════════════════════════════════

def _split_message(text: str, max_len: int = 4000) -> list:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


# ══════════════════════════════════════════════
#  Entry point: run all bots concurrently
# ═══════════════════════════════════════════════════════════════════════════

# Placeholder bot_token values that ship in system_config.example.json and
# indicate incomplete configuration. Mirrors feishu's _validate_bot_configs()
# style -- see issue #42 for the originating test report.
TELEGRAM_BOT_TOKEN_PLACEHOLDERS = {
    "YOUR_TELEGRAM_BOT_TOKEN",
    "your-telegram-bot-token",
    "your_telegram_bot_token",
    "TODO", "todo", "changeme", "replace_me", "replace-me",
    "0", "000000000:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
}

# A real Telegram bot_token looks like "<digits>:<35+ chars>". We use a
# pre-flight regex to flag obviously-wrong tokens (e.g. the 9-char "test123"
# or 4-char "demo") before sending them to api.telegram.org and getting
# a wrapped NetworkError that's hard to interpret.
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def _validate_bot_configs(bot_configs) -> bool:
    """Check for placeholder / malformed bot configs that would fail at runtime.

    Returns True if all configs look usable, False if any issue was found.
    Pre-flight is **advisory** -- callers should log the result and continue
    rather than abort, so a misconfigured bot never blocks the others.
    """
    ok = True
    for cfg in bot_configs:
        token = (cfg.bot_token or "").strip()
        if not token:
            logger.error(
                f"Bot [{cfg.name}] has empty bot_token. "
                f"Update system_config.json -> telegram.bots with a real token from @BotFather."
            )
            ok = False
            continue
        if token in TELEGRAM_BOT_TOKEN_PLACEHOLDERS:
            logger.error(
                f"Bot [{cfg.name}] bot_token is still a placeholder: '{token}'. "
                f"Update system_config.json -> telegram.bots with a real token from @BotFather. "
                f"Until then, this bot cannot start (httpx will get a wrapped NetworkError)."
            )
            ok = False
            continue
        if not _TELEGRAM_BOT_TOKEN_PATTERN.match(token):
            # Don't hard-fail on this -- format has changed in the past, and
            # some tests use short synthetic tokens. But flag it loudly.
            logger.warning(
                f"Bot [{cfg.name}] bot_token does not match the expected "
                f"'<digits>:<35+ chars>' format (got '{token[:6]}...{token[-4:]}'). "
                f"If the bot fails to start, regenerate the token via @BotFather."
            )
        if not cfg.agent_id or cfg.agent_id.lower() in {
            "default-001", "your-agent-id", "your_agent_id", "TODO", "changeme",
        }:
            logger.error(
                f"Bot [{cfg.name}] agent_id is unset or uses a placeholder: '{cfg.agent_id}'. "
                f"Create the agent in the Web UI first and put its real id here."
            )
            ok = False
    return ok


def _preflight_network(bot_configs) -> bool:
    """TCP-probe api.telegram.org (direct) or the configured proxy.

    Returns True if every bot's outbound path is reachable, False otherwise.
    Pre-flight is **advisory** -- callers log and continue.
    """
    import socket
    from urllib.parse import urlparse

    ok = True
    for cfg in bot_configs:
        if cfg.proxy:
            url = urlparse(cfg.proxy)
            host = url.hostname
            port = url.port or (443 if (url.scheme or "").lower() in ("https", "socks5h") else 80)
            try:
                with socket.create_connection((host, port), timeout=5):
                    pass
                logger.info(
                    f"Bot [{cfg.name}] proxy reachable: {host}:{port}"
                )
            except (OSError, socket.timeout) as e:
                logger.error(
                    f"Bot [{cfg.name}] proxy {host}:{port} is NOT reachable ({e!r}). "
                    f"Check telegram.bots[].proxy / TELEGRAM_PROXY env / system_config.json -> telegram.proxy. "
                    f"Without a working proxy, all getMe / long-poll requests will fail."
                )
                ok = False
        else:
            try:
                with socket.create_connection(("api.telegram.org", 443), timeout=5):
                    pass
                logger.info(
                    f"Bot [{cfg.name}] direct path to api.telegram.org:443 reachable"
                )
            except (OSError, socket.timeout) as e:
                # Direct path failure is a WARNING, not an ERROR: the user may
                # legitimately run in an environment that requires a proxy, and
                # we don't want to block startup in that case. _validate_bot_configs
                # will catch a configured-but-broken proxy.
                logger.warning(
                    f"Bot [{cfg.name}] direct path to api.telegram.org:443 NOT reachable "
                    f"({e!r}). If you are in a region / network that blocks Telegram, "
                    f"set telegram.proxy (e.g. 'socks5://user:pass@host:1080') or the "
                    f"TELEGRAM_PROXY environment variable. _initialize_with_retry will "
                    f"still attempt getMe and may succeed once the proxy is configured."
                )
                # We do not mark ok = False here: the user can fix the proxy
                # at runtime and the existing retry/backoff handles the rest.
    return ok


def _run_preflight(bot_configs) -> None:
    """Combined preflight: validate configs first, then probe network.

    Logs are emitted with appropriate severity (ERROR vs WARNING) and the
    caller is expected to continue regardless -- this is a *signalling*
    mechanism, not a gate. The whole point is to give the user a clear
    "this is wrong" message at startup instead of an opaque
    ``telegram.error.NetworkError`` 60 seconds later.
    """
    config_ok = _validate_bot_configs(bot_configs)
    network_ok = _preflight_network(bot_configs)
    if not config_ok or not network_ok:
        logger.warning(
            "Telegram preflight detected issues. The adapter will still start, "
            "but the affected bots will likely fail at getMe / first message."
        )


async def run_all_bots():
    """Start all configured bots and keep running until interrupted."""
    bot_configs = load_bot_configs()

    if not bot_configs:
        logger.error("No enabled Telegram bots configured.")
        logger.error("Add bot entries to system_config.json -> telegram.bots")
        return

    # Pre-flight: surface config + network problems *before* we burn 60s on
    # the first getMe retry. See issue #42.
    _run_preflight(bot_configs)

    print("=" * 60)
    print("  OpenSquad Telegram Adapter (Multi-bot)")
    print("=" * 60)
    print(f"  Adapter URL:  {EXTERNAL_ADAPTER_URL}")
    print(f"  Bots:         {len(bot_configs)}")
    for i, cfg in enumerate(bot_configs):
        proxy_info = cfg.proxy or "(direct)"
        print(f"    [{i+1}] {cfg.name}: token={cfg.bot_token[:8]}...{cfg.bot_token[-4:]} -> agent={cfg.agent_id} proxy={proxy_info}")
    print("=" * 60)

    runners: list[TelegramBotRunner] = []

    for cfg in bot_configs:
        runner = TelegramBotRunner(cfg)
        runners.append(runner)

    # Start all bots
    for runner in runners:
        try:
            await runner.start()
        except Exception as e:
            logger.error(f"Failed to start bot [{runner.cfg.name}]: {e}", exc_info=True)

    active = [r for r in runners if r.application]
    if not active:
        logger.error("No bots started successfully.")
        return

    logger.info(f"{len(active)} bot(s) running. Press Ctrl+C to stop.")

    # Keep running until interrupted
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Shutting down all bots...")
        for runner in active:
            await runner.stop()
        logger.info("All bots stopped.")


def main():
    # Check if service is enabled
    if not is_service_enabled():
        logger.info("Telegram service is disabled (services.telegram.enabled=false). Exiting.")
        sys.exit(0)

    try:
        asyncio.run(run_all_bots())
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
