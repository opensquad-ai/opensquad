"""TUI UI locale (en / zh). Default English; persisted under ~/.opensquad/."""

from __future__ import annotations

import json
from pathlib import Path

LOCALE_PREF_PATH = Path.home() / ".opensquad" / "cli_locale.json"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "zh")

_LOCALE_ALIASES: dict[str, str] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en_us": "en",
    "zh": "zh",
    "cn": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "zh-hans": "zh",
    "chinese": "zh",
    "中文": "zh",
}

# key → {en, zh}
_STRINGS: dict[str, dict[str, str]] = {
    "ctrl_c_copied": {
        "en": "Copied · press Ctrl+C again to exit",
        "zh": "已复制 · 再按 Ctrl+C 退出",
    },
    "ctrl_c_cleared": {
        "en": "Cleared · press Ctrl+C again to exit",
        "zh": "已清空 · 再按 Ctrl+C 退出",
    },
    "ctrl_c_stopped": {
        "en": "Stopped · next Enter resumes (queue held) · Ctrl+C again to exit",
        "zh": "已停止 · 下次回车继续（队列已保留）· 再按 Ctrl+C 退出",
    },
    "ctrl_c_stopped_held": {
        "en": "Stopped · {n} queued held · next Enter sends all together · Ctrl+C again to exit",
        "zh": "已停止 · 保留 {n} 条排队 · 下次回车一并发送 · 再按 Ctrl+C 退出",
    },
    "ctrl_c_again": {
        "en": "Press Ctrl+C again to exit",
        "zh": "再按一次 Ctrl+C 退出",
    },
    "notify_space_toggle": {
        "en": "Press Space to toggle options first",
        "zh": "先按 Space 勾选选项",
    },
    "custom_answer_banner": {
        "en": "Custom answer — type then Enter · Esc back to list",
        "zh": "自定义答案 — 输入后 Enter 提交 · Esc 返回列表",
    },
    "hint_slash_menu": {
        "en": "↑↓ select · Tab autocomplete · Enter run · Esc close",
        "zh": "↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭",
    },
    "hint_nav_push": {
        "en": "{title} — ↑↓ select · Enter open/run · Esc back",
        "zh": "{title} — ↑↓ 选择 · Enter 进入/执行 · Esc 返回",
    },
    "hint_nav_paint": {
        "en": "↑↓ select · Enter/Tab confirm · Esc back/close",
        "zh": "↑↓ 选择 · Enter/Tab 确认 · Esc 返回/关闭",
    },
    "hint_session_picker": {
        "en": "↑↓ select · Enter/Tab switch · Esc cancel · /session <n> jump",
        "zh": "↑↓ 选择 · Enter/Tab 切换 · Esc 取消 · /session <n> 直达",
    },
    "hint_mention_menu": {
        "en": "↑↓ select · Tab/Enter insert @name · Esc close",
        "zh": "↑↓ 选择 · Tab/Enter 插入 @成员 · Esc 关闭",
    },
    "mention_menu_title": {
        "en": "Mention member",
        "zh": "提及成员",
    },
    "placeholder_group": {
        "en": "Message {gname}…  @ · Tab · ^E ^O ^X ^p · /leave",
        "zh": "发给 {gname}…  @ · Tab · ^E ^O ^X ^p · /leave",
    },
    "decision_custom_title": {
        "en": "Type your own answer",
        "zh": "输入自定义回复",
    },
    "decision_custom_desc": {
        "en": "Enter a custom reply",
        "zh": "输入自定义回复",
    },
    "decision_please_select": {
        "en": "Please choose:",
        "zh": "请选择：",
    },
    "decision_mode_switch": {
        "en": "Switch mode {from_mode} → {to_mode}",
        "zh": "切换模式 {from_mode} → {to_mode}",
    },
    "decision_switch_to": {
        "en": "Switch to {to_mode}",
        "zh": "切换到 {to_mode}",
    },
    "decision_deny_mode": {
        "en": "Decline this mode switch",
        "zh": "拒绝此次模式切换",
    },
    "decision_approve_desc": {
        "en": "Approve",
        "zh": "批准",
    },
    "decision_reject_desc": {
        "en": "Reject",
        "zh": "拒绝",
    },
    "decision_hint_select": {
        "en": "↑↓ select · enter submit · esc dismiss",
        "zh": "↑↓ 选择 · Enter 提交 · Esc 关闭",
    },
    "decision_hint_multi": {
        "en": "↑↓ · space toggle · enter submit · esc dismiss",
        "zh": "↑↓ · Space 勾选 · Enter 提交 · Esc 关闭",
    },
    "decision_hint_custom": {
        "en": " · custom allowed",
        "zh": " · 可选自定义",
    },
    "wait_thinking": {
        "en": "Thinking…",
        "zh": "思考中…",
    },
    "wait_connecting": {
        "en": "Connecting…",
        "zh": "连接中…",
    },
    "wait_replying": {
        "en": "Replying…",
        "zh": "回复中…",
    },
    "wait_working": {
        "en": "Working…",
        "zh": "处理中…",
    },
    "wait_preparing": {
        "en": "Preparing…",
        "zh": "准备中…",
    },
    "wait_boot_services": {
        "en": "Starting services…",
        "zh": "正在启动服务…",
    },
    "wait_boot_agent": {
        "en": "Starting agent {name}…",
        "zh": "正在启动 agent {name}…",
    },
    "wait_boot_ready": {
        "en": "Waiting for {name}… {elapsed}s · {status}",
        "zh": "等待 {name} 就绪… {elapsed}s · {status}",
    },
    "wait_boot_ws": {
        "en": "Connecting to {name}…",
        "zh": "正在连接 {name}…",
    },
    "wait_boot_ready_ok": {
        "en": "Agent ready",
        "zh": "Agent 已就绪",
    },
    "boot_agent_pending": {
        "en": "Agent '{name}' is still starting — wait or /start",
        "zh": "Agent '{name}' 仍在启动中 — 请稍候或执行 /start",
    },
    "thinking_label": {
        "en": "Thinking:",
        "zh": "思考：",
    },
    "detail_fold_hint": {
        "en": "… (^O expand)",
        "zh": "…（^O 展开）",
    },
    "detail_on": {
        "en": "Detail view ON — full thinking & tools",
        "zh": "详细模式已开启 — 显示完整思考与工具",
    },
    "detail_off": {
        "en": "Detail view OFF — compact thinking & tools",
        "zh": "详细模式已关闭 — 折叠思考与工具",
    },
    "debug_on": {
        "en": "Debug mode ON — verbose system logs",
        "zh": "调试模式已开启 — 显示详细系统日志",
    },
    "debug_off": {
        "en": "Debug mode OFF — clean production view",
        "zh": "调试模式已关闭 — 简洁生产视图",
    },
    "wait_loading": {
        "en": "Loading {kind}…",
        "zh": "加载 {kind}…",
    },
    "placeholder_ask": {
        "en": "Ask anything…  Tab · ^E ^O ^X ^p",
        "zh": "随便问…  Tab · ^E ^O ^X ^p",
    },
    "placeholder_agent": {
        "en": "Message {agent}…  Tab · ^E ^O ^X ^p",
        "zh": "发给 {agent}…  Tab · ^E ^O ^X ^p",
    },
    "placeholder_api_key": {
        "en": "Paste API key…  Enter save · Esc cancel",
        "zh": "粘贴 API key…  Enter 保存 · Esc 取消",
    },
    "placeholder_field": {
        "en": "Enter {fld}…  Enter save · Esc cancel",
        "zh": "输入 {fld}…  Enter 保存 · Esc 取消",
    },
    "placeholder_custom": {
        "en": "Type your own answer…  Enter submit · Esc back",
        "zh": "输入自定义回复…  Enter 提交 · Esc 返回",
    },
    "placeholder_login_email": {
        "en": "Email…  Enter continue · Esc cancel",
        "zh": "邮箱…  Enter 继续 · Esc 取消",
    },
    "placeholder_login_password": {
        "en": "Password…  Enter login · Esc cancel",
        "zh": "密码…  Enter 登录 · Esc 取消",
    },
    "login_ask_email": {
        "en": "Enter email, then password (Esc to cancel)",
        "zh": "请输入邮箱，然后输入密码（Esc 取消）",
    },
    "login_ask_password": {
        "en": "Enter password for {email} (Esc to cancel)",
        "zh": "请输入 {email} 的密码（Esc 取消）",
    },
    "login_ask_name": {
        "en": "No web account yet — enter a display name for {email} (Esc to cancel)",
        "zh": "首次使用需注册 — 请输入 {email} 的显示名称（Esc 取消）",
    },
    "login_email_required": {
        "en": "Email required",
        "zh": "邮箱不能为空",
    },
    "login_cancelled": {
        "en": "Login cancelled",
        "zh": "已取消登录",
    },
    "login_working": {
        "en": "Signing in…",
        "zh": "正在登录…",
    },
    "login_ok": {
        "en": "Logged in — {name} <{email}>",
        "zh": "登录成功 — {name} <{email}>",
    },
    "login_failed": {
        "en": "Login failed: {err}",
        "zh": "登录失败：{err}",
    },
    "boot_tip": {
        "en": "[dim]Tab Plan/Build · ^E effort · ^X live · /theme · /language · /model Connect[/]\n",
        "zh": "[dim]Tab 计划/构建 · ^E 深度 · ^X 实况 · /theme · /language · /model 接入[/]\n",
    },
    "header_welcome": {
        "en": "Welcome to OpenSquad! Send /help for help.",
        "zh": "欢迎使用 OpenSquad！发送 /help 查看帮助。",
    },
    "header_project": {
        "en": "Project",
        "zh": "项目",
    },
    "language_menu_title": {
        "en": "Language · esc to close",
        "zh": "语言 · Esc 关闭",
    },
    "language_set": {
        "en": "Language → {code}",
        "zh": "语言 → {code}",
    },
    "language_unknown": {
        "en": "Unknown language '{name}'. Try /language en|zh",
        "zh": "未知语言 '{name}'。试试 /language en|zh",
    },
    "language_active": {
        "en": "active",
        "zh": "当前",
    },
    "language_label_en": {
        "en": "English",
        "zh": "English",
    },
    "language_label_zh": {
        "en": "中文 (Chinese)",
        "zh": "中文",
    },
    "mode_plan": {
        "en": "Plan",
        "zh": "计划",
    },
    "mode_build": {
        "en": "Build",
        "zh": "构建",
    },
    "screen_cleared": {
        "en": "Screen cleared",
        "zh": "已清空屏幕",
    },
    "not_logged_in": {
        "en": "[yellow]Not logged in — /login[/]",
        "zh": "[yellow]未登录 — /login[/]",
    },
    "no_autostart_agent": {
        "en": "No auto-start agent. /start <name>  or  /autostart <name>  (synced with Web)",
        "zh": "未设置默认启动 Agent。/start <名称>  或  /autostart <名称>（与网页「设为默认启动」同步）",
    },
}

_current: str = DEFAULT_LOCALE


def normalize_locale(token: str | None) -> str | None:
    raw = (token or "").strip().lower().replace(" ", "")
    if not raw:
        return None
    if raw in _LOCALE_ALIASES:
        return _LOCALE_ALIASES[raw]
    # accept bare "zh-*" / "en-*"
    if raw.startswith("zh"):
        return "zh"
    if raw.startswith("en"):
        return "en"
    return None


def get_locale() -> str:
    return _current if _current in SUPPORTED_LOCALES else DEFAULT_LOCALE


def set_locale(code: str, *, persist: bool = True) -> str:
    global _current
    norm = normalize_locale(code) or DEFAULT_LOCALE
    _current = norm
    if persist:
        save_locale(norm)
    return norm


def load_saved_locale() -> str:
    try:
        if LOCALE_PREF_PATH.is_file():
            data = json.loads(LOCALE_PREF_PATH.read_text(encoding="utf-8"))
            code = normalize_locale(str((data or {}).get("locale") or ""))
            if code:
                return code
    except Exception:
        pass
    return DEFAULT_LOCALE


def save_locale(code: str) -> None:
    norm = normalize_locale(code)
    if not norm:
        return
    try:
        LOCALE_PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCALE_PREF_PATH.write_text(
            json.dumps({"locale": norm}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def t(key: str, **kwargs: object) -> str:
    """Translate a UI string for the active locale."""
    entry = _STRINGS.get(key) or {}
    locale = get_locale()
    text = entry.get(locale) or entry.get(DEFAULT_LOCALE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def locale_display_name(code: str) -> str:
    norm = normalize_locale(code) or code
    if norm == "zh":
        return t("language_label_zh")
    return t("language_label_en")
