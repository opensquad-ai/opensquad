---
name: playwright
description: Direct browser automation (navigate, click, type, screenshot, evaluate) using Playwright — no MCP server required.
allowed-tools: filesystem, web
---

# Playwright Browser Automation

Driver for performing real web browser interactions (UI testing, form filling,
page scraping, screenshot capture) directly through the Playwright Python SDK.
This replaces the old `@playwright/mcp` server, so **no MCP subprocess is
launched** — the tools here call Chromium in-process.

## When to use

- You need to interact with a live web page: visit a URL, click buttons, fill
  forms, press keys, read rendered text.
- You want a screenshot of a page or region.
- You need to run custom JavaScript to extract or mutate page state.

## Workflow

Follow this order for typical browser tasks:

1. **Open a page** — `browser_navigate(url)` (e.g. `browser_navigate(url="https://example.com")`).
2. **Inspect what's there** — `browser_snapshot()` returns the current URL, title and visible text.
3. **Act** — `browser_click(selector=...)`, `browser_type(selector=..., text=...)`,
   `browser_select(selector=..., value=...)`, `browser_press_key(key="Enter")`, or `browser_evaluate(script=...)`.
4. **Re-check** — call `browser_snapshot()` again to confirm the page changed.
5. **Capture** (optional) — `browser_screenshot(path="result.png", full_page=True)`.
6. **Clean up** — `browser_close()` when fully done to free the headless browser.

## Tool reference

- `browser_navigate(url, timeout=30000, wait_until='domcontentloaded', ready_selector=None)` — load a URL. `wait_until` ∈ `load|domcontentloaded|networkidle|commit` (use `networkidle` for SPAs that hydrate after DOMContentLoaded). `ready_selector` waits for that element before returning.
- `browser_snapshot()` — visible text of the current page (state check).
- `browser_click(selector, timeout=10000)` — `selector` is a CSS selector, or visible text. Raises a clear timeout if nothing matches (never silently "succeeds").
- `browser_type(selector, text, submit=None)` — fill an input; `submit=True` presses Enter, a CSS/text `submit=` clicks that send button (for UIs where Enter inserts a newline instead of sending).
- `browser_press_key(key)` — e.g. `"Enter"`, `"Tab"`, `"Escape"`, `"ArrowDown"`.
- `browser_select(selector, value)` — pick an `<option value=...>`.
- `browser_wait_for(selector, state='visible', timeout=30000)` — wait until a selector reaches `attached|detached|visible|hidden`.
- `browser_wait_for_text(selector=None, stable_for=1500, timeout=60000)` — wait until text content stops changing. Use it to know a **streaming chat answer has finished** (e.g. DeepSeek), instead of polling yourself.
- `browser_evaluate(script)` — run JS and return the value as a string.
- `browser_screenshot(path=None, full_page=True)` — if `path` is given it is used **exactly as-is** (relative → cwd). With no path it saves under `data/browser_screenshots/screenshot.png`. `full_page=True` captures scrolled-out content too.
- `browser_save_state()` — explicitly persist cookies + localStorage to disk now.
- `browser_wait(milliseconds=1000)` — settle before the next action.
- `browser_go_back()` — previous page.
- `browser_close()` — end the session.

## Persistence (跨会话登录)

The browser keeps a **persistent profile** so logins and session state survive
`browser_close()`. This means once you log in to a site, later browser sessions
reopen it **without re-authenticating**.

- **Profile dir — 机器级共享（不是工作区私有）**: the login profile lives in a
  **fixed, machine-wide shared folder** decoupled from your cwd / project / workspace:
  `%USERPROFILE%\.opensquad_playwright\browser_profile` (Linux/macOS
  `~/.opensquad_playwright/browser_profile`). Every agent on this machine points to
  the SAME profile, so whoever logs in first (or an admin's migration) makes the login
  available to all of them — **no more repeated logins per agent**. Override with the
  `PLAYWRIGHT_PROFILE_DIR` env var to force a different shared location.
- **Session state**: cookies + localStorage are serialized to
  `<profile>/storage_state.json` **three times** — on `browser_close()`, on an
  explicit `browser_save_state()`, and automatically (debounced ~10s) in the
  background after each browser call. So even if the process is killed by OOM /
  Ctrl-C / crash, you only lose at most ~10s of session, not the whole login.
- **环境变量语义**: `OPENSQUAD_WORKSPACE` only relocates *screenshots / temp data*
  (the workspace data dir). It **no longer** moves the login profile — the shared
  profile stays put regardless of cwd or that variable. To switch the shared profile
  explicitly, use `PLAYWRIGHT_PROFILE_DIR` (and do so **before** the skill starts;
  changing it mid-process switches to that folder's profile).
- **Why both?** Some sites (e.g. DeepSeek) issue *session cookies* (no expiry)
  that Chromium clears on restart even with a `user_data_dir`. The `storage_state`
  snapshot is what actually restores those session cookies and localStorage, so
  the login survives a full close + reopen. Verified end-to-end against
  DeepSeek: login → close → reopen stays logged in.
- **Headless by default**: visible UI tasks (manual login, CAPTCHA) may need
  `PLAYWRIGHT_HEADLESS=0` so the human can log in once; the profile then makes it
  persist for later automated runs.

## DeepSeek AI 搜索问答（信息搜索）

Agent 对任何**需要了解、但自己不知道**的信息，都可以把本 skill 当作搜索工具：打开 DeepSeek 网页版、开启智能搜索、提问并等回答结束。这是通用信息搜索，不限于某个业务步骤。

一个高频且重要的场景：用 DeepSeek 联网搜索并得到 AI 生成的回答。这是把浏览器当作**信息搜索/答案工具**来用，而不是做 UI 测试。核心难点不是提问，而是**等 AI 把流式回答吐完**——不能用固定的 `browser_wait`，要用"内容稳定"判断我认为答案已生成完毕。

典型流程（已端到端实测通过）：

1. **打开并等就绪**
   `browser_navigate(url="https://chat.deepseek.com", ready_selector="textarea")`
   `ready_selector` 等输入框出现，等于告诉工具"React 已经 hydrate 完，可以交互了"。
2. **确保是新对话**（可选，避免接在旧上下文后）
   `browser_click("开启新对话")` —— 文本点击现在真实生效，能关掉非最新对话。
3. **打开联网搜索**（可选，用 `browser_evaluate` 切换开关）
   `browser_evaluate(script="...定位搜索开关并 click()...")`
   页面元素/开关的选择器容易漂移，优先用能唯一识别的文本或 JS 属性。
4. **提问**
   `browser_type(selector="textarea[placeholder*='消息']", text="当前A股行情怎么样？", submit=True)`
   当前页 placeholder 是「给 DeepSeek 发送消息」一类；旧版才是「任何话题」。
   不确定就先 `browser_evaluate` 取可见 `textarea` 的 placeholder，或直接 `textarea`。
   `submit=True` 按 Enter。**不要**用 `submit="发送"` —— 发送按钮常无稳定可见文本，会干等到超时。
   若怀疑没发出：看输入框是否已清空；仍有长文本再用 JS 点发送按钮。
5. **等待 AI 回答完毕（关键，最容易踩坑）**
   - `browser_wait_for_text(selector=".ds-markdown", stable_for=2000, timeout=90000)`
   - ⚠️ **它盯的是"最新一条"（last），不是第一条（first）**。在一个对话框里连问过多次、出现多条 `.ds-markdown` 后，如果用 `.first` 就会取到**旧回答**（早已稳定）→ 秒回，但最新回答才刚开头 → 拿到半截。本工具默认已用 last，**多轮对话时这一条尤其关键**。
   - 可选：如知道"停止生成"控件的选择器，传 `stop_selector="..."`，会额外要求它消失才算完成（防长思考时的假停顿）。
6. **取回答**
   取最后一个 `.ds-markdown`。`innerText` 会把网页引用角标拼成 `-2-9` 这类残片，
   先 clone 节点、删掉 `sup` / 纯数字链接 / cite 节点再取文本。
   📌 **必须取最后一个 `.ds-markdown`**（`querySelector('.ds-markdown')` 只会给你**最早的那条**旧回答）；如页面只显示"开启后的最新对话"也可用 `browser_snapshot()` / `browser_screenshot(path, full_page=True)` 留存。
7. **收尾**
   `browser_save_state()` 保住登录态（即使进程被杀也不再丢）。

**注意事项**
- 上面 `.ds-markdown`、`textarea` 等会随 DeepSeek 前端变。工具先按 CSS 定位；**CSS 匹配不到就立即失败**，不会把选择器字符串当可见文本干等。可见文本定位仍适用于 `开启新对话` 这类不含 CSS 符号的文案。先 `browser_snapshot()` 再定位最稳。
- 登录态靠持久化 profile 保住，首次请在 `PLAYWRIGHT_HEADLESS=0` 下人工登录一次（含验证码），之后自动化 `ready_selector="textarea"` 即可直接提问。
- 等不到结束就取文本，会拿到半截回答。**没有走完第 5 步前不要取答案**。

## Notes

- A single persistent Chromium is shared across calls; state (cookies,
  scroll, navigation) persists until `browser_close()` and across sessions.
- Prefer precise CSS selectors. When you're unsure, use `browser_snapshot()`
  first, then target by visible text.
- `browser_evaluate` is powerful — use it to extract data structures (e.g.
  `script="document.querySelectorAll('article').length"`) or scroll
  (`script="window.scrollTo(0, document.body.scrollHeight)"`).
- If tool calls report a `playwright` dependency error, install it with
  `pip install playwright playwright-stealth && python -m playwright install chromium`.
