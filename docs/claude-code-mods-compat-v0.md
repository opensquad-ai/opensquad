# OpenSquad × Claude Code Mods 兼容性矩阵 v0

> **状态：纸面设计，未实现。** 这份文档是阶段 0 的产物——在动任何运行时之前，先把"我们服务什么、降级什么、拒绝什么"写清楚。每一项都带依据和置信度，**不要**把它当成已实现能力的描述。
>
> **上游依据**：Claude Code Mods（官方 `plugins/mods/reference`，v2.1.287 线）× DeepSeek Harness 的兼容层（`deepseek-ai/deepseek-harness`，MIT，v0.2.1-alpha.1）作为第二意见。dsh 的价值有两块：一是它**已经用真实 mod 跑过一遍并公开了差异**；二是它把宿主建在 cordis 上，而 **cordis 是独立的 MIT 上游**（`github.com/cordiverse/cordis`）——可以被我们当依赖直接用。"dsh 抄不动"的原判断已更新，见 §8。
>
> **置信度标记**：`✅` 已在代码/官方文档中核实 · `⚠️` 待确认（阶段 1 落地时必须验证） · `❌` 明确不做。

---

## 0. 三条铁律

这三条是**产品决策**，不是技术限制。任何"开插槽"的讨论都必须先满足它们：

1. **安全交互的插槽永不开放**。审批流、权限确认、邀请确认、危险操作确认——mod 不得渲染、不得介入。Claude Code 同样守着这条（"mod 不能修改权限确认框"），而 OpenSquad 的 UI 里真的有这类交互，理由更强。
2. **交互回调一律宿主代理**。mod 交出的是声明，不是句柄。按钮触发走宿主侧的 action id（dsh 的做法：`SurfaceHost.runAction(sessionId, callback)`，"the host owns the invocation and its reporting"）。
3. **元素树必须受校验**。白名单元素 + 属性白名单 + 尺寸/深度上限。否则一个 mod 就能画一块假的审批卡骗用户。

---

## 1. 对照列：mods 的权威契约

以下全部来自官方 reference（`zh-CN/plugins/mods/reference`），保留原始标识符。

### 1.1 事件（注册用，官方 reference 列出 47 个）

| 类别 | 事件 |
|---|---|
| 工具 | `tool.call` `tool.check` `tool.describe` |
| 提示词 | `prompt.submit` `prompt.fill` `prompt.suggest` `prompt.edit` `prompt.compose` `prompt.section` `prompt.context` `prompt.attachment` |
| 其他文本 | `skill.prompt` `attribution.text` |
| 命令/配置 | `command.run` `command.describe` `config.set` `config.describe` |
| 轮次 | `turn.start` `turn.step` `turn.complete` |
| 会话 | `session.start` `session.end` `session.compact` `session.receive` `session.send` `session.append` `session.attach` `session.detach` `session.measure` |
| 智能体 | `agent.offer` `agent.spawn` |
| UI | `ui.render` `ui.resolve` `ui.press` `ui.input` `ui.select` `ui.focus` `ui.scroll` `ui.close` `ui.message` `ui.fault` |
| 引擎/插件 | `plugin.register` `engine.create` |
| 遥测 | `telemetry.log` `telemetry.mark` |
| 兼容 | `classic.Stop` `classic.PostToolUse` |

> 注：dsh 的兼容表还把 `tool.list` / `tool.register` 列为事件（并注明"不抛，只作为 `$` 调用服务"），官方 zh reference 未列这两个——属上游版本漂移，**以官方清单为准**。

### 1.2 `$` 能力（共 21 个命名空间）

```
$.plugin.{name,root}
$.ui.{resolve,invalidate,open,close,panes,focus,scroll,toast,status,log,notice,ask,copy,selection,blit}
$.command.{register,run,list}
$.tool.{register,call,check,list}
$.agent.{register,spawn,list}
$.model.{complete,fork,classify}
$.prompt.{submit,read,fill,suggest,compose}
$.turn.abort
$.session.{messages,cwd,root,model,turns,id,repo,surfaces,usage,version,compact,send,append,authorize}
$.config.{list,set}
$.settings.read
$.env.{get,set}
$.fs.{read,write,list,exists,stat,ancestors}
$.store.{get,set,delete,keys}
$.state.{get,set,atom,read,update,derive,memberOf}
$.clock.{now,sleep,after,every}
$.http.fetch
$.process.{run,spawn}
$.mcp.{call,connect}
$.audio.{play,speak}
$.telemetry.{log,mark}
```

### 1.3 绘制元素（12 种）

`Box`(key/flex/gap/padding/margin/width/height/borderStyle/backgroundColor/position/hover) · `Text`(color/backgroundColor/bold/italic/underline/dimColor/inverse/wrap) · `Button`(key/label/onPress/hotkey/plain/dimColor/autoFocus/action) · `Link`(href/label) · `Code`(上限 10,000 字符) · `Markdown`(text 上限 10k/key/dimColor/onLinkPress/pressableLinks) · `Input` · `Select` · `Svg`(上限 131,072 字符) · `Client`(module/key) · `Raster`(columns ≤512, rows ≤256) · `Image`(≤2 MiB PNG/RGBA 或文件路径)

### 1.4 返回契约

`next(e)` · `{ deny: reason }` · `{ result }` · `{ value }` · `yield* next(e)`（async generator）· `{ text }` · `{ text: null }` · `{}` · `{ refusal: reason }` · `{ refused: reason }` · `{ consume: reason }` · `{ delivered: false, reason }`

### 1.5 限额

hook 自身 **10s** · `prompt.edit` **50ms** · `.catch` **1s** · `session.end` 全部钩子合计 **1.5s** · `$.process.run` 默认 **30s** / 上限 **10min** · `$.model.complete` maxTokens 默认 **1024** / 上限 **64,000** · `$.fs` 单次 **4 MiB** · `$.store` 每插件 **4 MiB** · `$.session.messages()` **4,096** 条 · `$.ui.invalidate` **10 次/秒**（终端 30 次/秒）· `$.ui.toast` **4s** · `plugin test` **5s**

### 1.6 打包

`.claude-plugin/plugin.json`（清单）+ `hooks/hooks.json`（**必填 `modules` 数组**）+ 模块导出 `register(on, options)` + 可选 `types/index.d.ts`。marketplace 用 `.claude-plugin/marketplace.json`（`name` / `owner` / `plugins[]`，每项 `name` + `source`，git 源可 `ref`/`sha` 锁定）。

---

## 2. 裁定标准

| 裁定 | 含义 | 判据 |
|---|---|---|
| `served` | 语义等价，或差异对 mod 无感 | 宿主已有等价接缝，且预算、时序、返回契约都对得上 |
| `degraded` | 能跑，但行为与 Claude Code 有**已知且要写进文档**的差异 | 有接缝但语义偏移（时序/粒度/信息损失），或需要额外闸门 |
| `refused` | 注册时不报错，但运行时明确失败并**指明缺口** | 需要我们不打算提供的宿主私有设施，或触碰三条铁律 |

**一个原则**：`refused` 必须**在加载时就报出来**（dsh 就是"注册了但从不上抛，加载时给 warning"），不能等 mod 跑到一半莫名失败。

---

## 3. 事件映射

| mods 事件 | 裁定 | OpenSquad 接缝 | 差异与理由 |
|---|---|---|---|
| `tool.call` | **served** | `@hook.on_before_tool` / `on_after_tool` / `on_tool_error`（`plugin_api.py:399-452`） | 前/后观察、`{ deny }`、改写 result 都成立。**但**：Claude Code 里 `tool.call` 跑在权限检查**之前**且可改参数/改工具名；我们跑在工具执行**之后**的决策点，改参数/改工具名 → **降级为拒绝**（同 dsh 的取舍：调用已记录，不能改写已记录的调用） |
| `prompt.submit` | **degraded** | `@hook.on_message_received`（`plugin_api.py:345-360`） | 可观察、可 `{ deny }`（等价 `__stop__`）。改写 `e.text` 需经输入层改写，粒度是"整条消息"而非"人类自己的文本块"——`context` 追加块、`origin` 标记不具备 |
| `prompt.edit` | **refused** | — | 50ms 热路径 + 我们无行内编辑器协议。即使有桥也不可能（见 §7 跨进程预算） |
| `prompt.compose` / `section` / `context` / `attachment` / `fill` / `suggest` | **refused** | — | 我们没有可组合的提示词管线（提示词在 `context_builder.py` 内整体拼装，无分段回调） |
| `turn.start` / `turn.complete` | **served** | `@hook.on_task_start` / `on_task_complete`（`plugin_api.py:511-546`） | 语义对得上；`durationMs` 需由宿主注入（我们已记录 `thought_ms`） |
| `turn.step` | **degraded** | `on_after_tool` → 每轮工具后发一次 | **不是** Claude Code 的 async generator 流式语义：我们每轮工具调用后推一个 tick，没有"逐步产出"的中途回传。依赖 tick 做计时的 mod（cache-meter）可用，依赖逐 token 流式的不可用 |
| `session.start` / `session.end` | **degraded** | agent 启动/销毁阶段（`agent_boot_phases.py`） | 我们是 **agent 级**而非 **session 级**：一个 agent 进程可承载多个会话。语义要重新定义（v0 提案：映射到 agent 生命周期，文档明示差异） |
| `session.compact` / `receive` / `send` / `append` / `attach` / `detach` / `measure` | **refused** | — | 会话生命周期事件不在我们的 hook 面上 |
| `command.run` | **served** | 斜杠命令体系：`cli/slash_commands.py`（`COMMANDS`，34 条 + `SlashCommand` 数据类） | 我们已有 `name/help/usage/subcommands/aliases/category` 的命令模型，`$.command.register` 可自然扩展它 |
| `command.describe` | **degraded** | 同上 | 可提供静态描述；动态 describe 钩子无对应 |
| `ui.render` | **degraded** | 见 §5 插槽清单 | 只开等价于 `AbovePrompt` 的少数插槽；其余渲染位 refused |
| `ui.resolve` / `press` / `input` / `select` / `focus` / `scroll` / `close` / `message` / `fault` | **refused** | — | 需要输入接管与焦点管理，触碰铁律 1/2 |
| `telemetry.log` / `mark` | **degraded** | `EventBus`（`src/opensquad/events.py`）+ `token_analytics` 插件 | 我们已有事件总线与 token 统计落库；字段集合不同 |
| `tool.check` / `describe` / `list` | **refused** | — | 我们的工具清单由 `ToolRegistry` 静态决定（`registry.py`），无逐次权限判定回调 |
| `tool.register`（事件） | **degraded** | `$.tool.register`（见 §4） | 事件形式不给，能力形式给 |
| `skill.prompt` | **degraded** | skills 体系（`src/skills/`、`SKILL.md`） | 语义相近，字段不同 |
| `attribution.text` | **refused** | — | 无对应概念 |
| `config.set` / `describe` | **degraded** | `syscfg` + 插件 `config_schema`（`launcher_main.py`、`_plugins.py`） | 我们有配置面，但它是"声明式 schema + UI"，不是事件钩子 |
| `agent.offer` / `spawn` | **refused** | — | 有子代理与 agent_factory，但无 `agent.offer` 这类编排协议 |
| `plugin.register` / `engine.create` | **refused** | — | 引擎级扩展点，v0 明确不开 |
| `classic.Stop` / `classic.PostToolUse` | **refused** | — | 兼容层概念，与我们无关 |

---

## 4. `$` 成员映射

| `$` 成员 | 裁定 | OpenSquad 接缝 | 差异 / 限额 |
|---|---|---|---|
| `$.plugin.name` / `root` | **served** | 插件目录（`collect_plugin_dirs`） | |
| `$.session.id` | **degraded** | hook 上下文里的 `sid`（由 `_turn_loop` / `turn_result_handler` 传入，插件缓存最近一次） | 只给**当前会话** id，没有 CC 的历史会话概念；M0 时 `Context` 里根本没有会话事实，是 P3 才补进来的 |
| `$.session.cwd` | **served** | `syscfg.get_workspace()` | |
| `$.session.root` / `model` / `turns` | **degraded** ⚠️ | 未接入（hook ctx 不带模型与轮次；`Context` 里也没有会话事实） | **M1 实测后改判**：原先是 served，但它们至今没接上，而且 `root` 与 `cwd` 在我们这儿同值（没有项目根/仓库根的分层）→ 降级并写明"未接入"，`WIRED` 不声明它 |
| `$.session.messages()` | **degraded** | 会话历史 ⚠️ | 上限沿用 4,096 条；裁剪/脱敏策略待定 |
| `$.session.usage` | **refused** | — | **M1 实测后改判**：真要它的两个真 mod（usage-meter、cache-panel）要的是 **Claude 账户的 rateLimits**（套餐窗口），还用 `$.http.fetch` 带 OAuth 句柄去调 Anthropic 的用量 API —— 本机没有对应物；返回 `{rateLimits: []}` 只会画一个空表，等于假数据 |
| `$.session.version` / `repo` / `surfaces` | **degraded** | 部分可给 ⚠️ | `surfaces` 只有 §5 开放插槽的子集 |
| `$.session.compact` / `send` / `append` / `authorize` | **refused** | — | 会话写入与授权是宿主特权 |
| `$.state.get` / `set` / `delete` / `keys` / `update` | **degraded** | 宿主进程内的 per-mod KV（`host.mjs` 的 `stateFor`） | **作用域是 agent 进程，不是会话**：一个宿主服务整个 agent，所以状态活到 agent 结束（CC 是会话级）。`atom` / `derive` / `memberOf` / `read` 未实现——用它们的 TS mod 走 SDK shim（P6） |
| `$.store.*` | **served** | `data/plugins/<name>/` | **4 MiB/插件**（照抄上游限额） |
| `$.clock.*` | **served** | asyncio | 定时器随插件/会话销毁 |
| `$.fs.*`（`read`/`write`/`list`/`exists`/`stat`） | **degraded** | 宿主 fs + **闸门** | 4 MiB/次；默认**拒绝写**，逐 mod 授权 |
| `$.fs.ancestors` | **refused** | — | 不提供 |
| `$.process.run` | **degraded** | `subprocess` + **闸门** | 默认 30s / 上限 10min（照抄）；默认拒绝，逐 mod 授权（能执行命令 = 等于给了 shell） |
| `$.process.spawn` | **refused** | — | 常驻子进程不给 |
| `$.http.fetch` | **degraded** | 宿主 HTTP + **闸门** | 4 MiB 响应上限；默认拒绝，需**域名白名单** |
| `$.env.get` / `set` | **degraded** | 进程环境 | `get` 给；`set` 默认拒绝（污染全局） |
| `$.command.register` / `list` | **served** | `cli/slash_commands.py` 的**运行时注册表**（`register_runtime_command` / `all_commands`，与静态 `COMMANDS` 合并，`command_source()` 标 source） | 注册项在 **CLI/TUI** 的补全与 `/help` 里可见；**Agent Web 仍无命令面**——agent 与网关都不处理 `/` 开头的消息（今天只有 CLI/TUI 调 `dispatch_slash`），要给 Web 做得单独做一层 |
| `$.command.run` | **degraded** | —（明确拒绝） | agent 进程里**没有 CLI dispatcher**，宿主无法代 mod 执行别人的命令；调用拿到明确错误而不是静默失败 |
| `$.tool.register` / `call` / `list` | **degraded** | `ToolRegistry.register`（`registry.py:257`） | `register` 给（命名空间化）；`call` 给；`list` 给。工具的 `level` 用我们现有的 `core`/`extended`/`hidden` |
| `$.prompt.submit` | **degraded** | `input_hub.py` / `message_router` ⚠️ | 提交的消息需标 `origin`（哪个 mod 发的），否则会与真人消息混淆 |
| `$.prompt.read` / `fill` / `suggest` / `compose` | **refused** | — | 无 compose 管线 |
| `$.turn.abort` | **degraded** | 停止任务（`/stop` 通路） | 语义对得上，粒度待确认 |
| `$.model.*`（`complete`/`fork`/`classify`） | **refused**（v0） | 模型层存在 ⚠️ | **能力上可做**，但 `$.model.complete` 会引入 mod 驱动的 LLM 调用（成本、配额、审计都要先有答案）。列为 v1 候选 |
| `$.agent.*` | **refused**（v0） | agent_factory / 子代理 ⚠️ | 同上，需要编排与配额先定 |
| `$.config.*` / `$.settings.read` | **refused**（v0） | `syscfg` 存在 | 读配置 = 读密钥面，v0 不开 |
| `$.mcp.*` | **refused**（v0）⚠️ | 我们**已有** `mcp_query` 插件 | 大概率可 `served`，但要走我们的 MCP 管理器而非直连。待阶段 1 定 |
| `$.audio.*` | **refused**（v0）⚠️ | 我们有 `src/opensquad/audio/` | 存在接缝，v0 不打 |
| `$.telemetry.*` | **degraded** | `EventBus` | 与事件侧同一裁定 |
| `$.ui.*` | 见 §5 | — | |

---

## 5. 插槽清单（渲染）

**核心立场**：插槽可以多，但**每个插槽里能做什么受约束**。不提供"任意位置自定义渲染"。

### 5.1 开放插槽（v0 提案）

| 插槽名 | 对应 mods 渲染位 | Electron 落点（候选） | 允许元素 | 重绘时机 |
|---|---|---|---|---|
| `AbovePrompt` | `AbovePrompt`（dsh 的 band） | 输入区上方，`MessageInput.tsx` 外侧 ⚠️ | `Text` `Box` `Button` `Markdown` `Code` | `session.start`、`turn.complete`、`tool.call` 链结束、被读 `$.state` 变更、`$.ui.invalidate` |
| `ToolUse` | `ToolUse` | `ToolCallBlock.tsx` 尾部 | `Text` `Box` `Code` `Markdown` | 工具调用结束 |
| `AssistantMessage` | `AssistantMessage` | `StreamingMessage.tsx` / `MessageBubble.tsx` 尾部 | `Text` `Box` `Markdown` `Code` | 消息落定 |
| `StatusBar` | —（我们的） | `StatusBadge.tsx` / `RepoStatusBar.tsx` | `Text` `Box` | 状态变更 |
| `Spinner` | `Spinner` | `PulseDotsStatus.tsx` | `Text` | 工作态变更 |
| `Pane` | `Pane` | **已有的 pane 系统**：`utils/paneViews.ts`（`changes`/`files`/`terminal`/`browser` 是内置位） | `Text` `Box` `Button` `Markdown` `Code` | 同 `AbovePrompt` |
| `Sidebar` | —（我们的） | `Sidebar.tsx`（已有 `contributes.navigation` 通路）⚠️ | 静态项 | 安装/启用变更 |

`Pane` 这条值得注意：**我们已经有具名 pane 视图系统**，插件新增一个 pane id 比 Claude Code 生态里做 Pane 更自然。

### 5.2 降级插槽

| 插槽 | 降级方式 |
|---|---|
| `Pane`（需要焦点/滚动/输入时） | 只给只读渲染；`$.ui.open` 返回 `{ isPlaced: false, reason }`，mod 按上游约定退回 `AbovePrompt`——**与 dsh 完全一致** |

### 5.3 永不开放（铁律 1）

审批与安全交互的所有落点：`OptionsApprovalCard.tsx`、`ModeSwitchApprovalCard.tsx`、`CollabStepApprovalCard.tsx`、`GitConfirmModal.tsx`、`GroupAccessPanel.tsx`、`AuthScreen.tsx`、`SystemConfigPage.tsx`、`PluginManagerPage.tsx` **自身**（不能让 mod 画插件管理页）。

### 5.4 元素白名单（v0，保守）

| 元素 | v0 | 理由 |
|---|---|---|
| `Text` `Box` `Button` `Markdown` `Code` | ✅ 开放 | 纯展示；`Button` 走 action id 代理 |
| `Link` | ⚠️ 待定 | 需要 `onLinkPress` 的宿主代理与 URL 策略 |
| `Input` `Select` | ❌ 推迟 | 引入输入接管，v0 不打 |
| `Svg` / `Raster` / `Image` | ⚠️ 待定 | 需要尺寸/内容上限（照抄上游：Svg 128 KiB、Raster 512×256、Image 2 MiB） |
| `Client`（`module`） | ❌ **拒** | 等于让 mod 加载任意前端模块——这是"接管渲染管线"的真正入口，v0 不开，且**未来开之前必须先有沙箱与签名** |

### 5.5 属性白名单（v0）

允许：`Box` 的 `flexDirection` / `padding` / `paddingX` / `paddingY` / `gap` / `borderStyle` / `width` / `height`；`Text` 的 `color`（仅主题调色板名）/ `bold` / `italic` / `underline` / `dimColor` / `wrap`；`Button` 的 `label` / `hotkey` / `action`。
其余属性一律**丢弃并记一条诊断**（不报错整个树——照 dsh 的宽松策略）。

### 5.6 TUI（Textual）

我们有两套 UI（Electron + Textual `src/opensquad/cli/tui/`）。**同一棵树要写两个渲染器**——这是成本项，不是免费午餐（dsh 也为 Web 侧单独发了一个 `client-ui-claude-code-mods` 包，且只覆盖 `AbovePrompt`）。
v0 提案：**先只做 Electron 侧，TUI 记为 v1**，并在矩阵里对 TUI 会话标注"该 mod 的绘制不可用"。

---

## 6. 明确不做（非目标）

- **官方精品 mod**：`diff` / `agents-md` / `sec-default` / `telemetry`——dsh 也跑不了，它们的依赖（`$.settings.read`、`$.telemetry.*`、`engine.create`、已放置的 Pane、`ui.focus`/`ui.scroll`）在我们的 refused 清单里。**红利在长尾第三方小 mod。**
- **输入接管**：提示框按键、焦点、滚动、行内编辑。
- **`$.model.*` / `$.agent.*` / `$.config.*` / `$.mcp.*` / `$.audio.*`**：v0 全拒（有的是能力问题，有的是**成本/配额/审计还没答案**）。
- ~~**直接加载 TypeScript**：v0 只收编译好的 `.js` / `.mjs`~~ → **M1 P6 已作废这条**：`.ts` 在 **Node 24 上原生直跑**（类型擦除，零依赖）；`.tsx` 的 **JSX 在加载时被转译**（vendor 的单文件 sucrase，只做语法变换、不执行代码、绝不跑 mod 自带的构建脚本）。实测 5/5 真 mod 都能加载了。**代价**：加载时会往 mod 目录写一个 `<name>.mods-build.mjs` 构建产物（放在源文件旁边，mod 自己的相对导入才解析得对；已 gitignore，按 mtime 缓存）。

---

## 7. 版本、安全与闸门

**版本锁定**：矩阵绑死 mods reference 的版本（v2.1.287 线）。上游漂移时**重跑一遍矩阵**，而不是假装还能用。

**信任分级**（必须在阶段 1 之前定，不能事后补）：
- 安装即有：`$.plugin` `$.session`(读) `$.state` `$.store` `$.clock` `$.command.register` `$.tool.register` + 开放的插槽。
- **逐 mod 授权**：`$.fs.write` / `$.process.run` / `$.http.fetch` / `$.env.set`。
- 永不授予：审批插槽、`Client` 元素、会话写入与授权（`$.session.authorize`）。

**无沙箱要在 UI 里明说**。我们的插件本来就是"进程内任意 Python"；再叠一个"进程内任意 JS"，等于两个无沙箱执行面。**accepted risk 的前提是用户知情**。

**离线闸门**：`claude plugin validate` 的等价物——事件名、`$` 成员名、元素名、属性名**全都能离线校验**（它们是闭集）。装之前就拦下"这个 mod 用了我永远不提供的东西"，并告诉用户**缺哪一项**。

---

## 8. dsh 生态的三层可移植性

**问题**：除了兼容 mods，能不能直接吃 dsh（DeepSeek Harness）的开源插件生态？答案必须分层——dsh 的"插件生态"实际是三层，可移植性完全不同。

| 层 | 是什么 | 可移植性 | 结论 |
|---|---|---|---|
| **框架层** | `cordis` 本体（`github.com/cordiverse/cordis`，MIT，v4.0.0-rc.7）。插件 = `Service` 对象，或带 `inject` + `apply(ctx)` 的函数；`ctx` 是服务仓库；5 种派发模式 `emit`/`waterfall`/`parallel`/`serial`/`bail`（waterfall = around-middleware `(...args, next)`，不调 `next()` 即短路）；`ctx.effect()` / `ctx.on()` 提供可逆副作用 | **可移植**——独立上游、MIT、与 dsh 无绑定 | ✅ **npm 直接依赖**，钉 `4.0.0-rc.7`。它顺带补上本矩阵的三处硬洞：①显式 deny/改写（waterfall 短路）②预算归属（effect 生命周期）③可卸载注册（区别于我们现有"注册了就不会摘"） |
| **服务语义层** | dsh 自己的服务包：`ctx.tools` / `ctx.llm` / `ctx.sessions` / `ctx.agents` / `ctx.settings` / `ctx.credentials` / `ctx.mcpResources` / `ctx.pluginManager` | **不可移植**——这是 dsh 内部语义，真实插件全绑死在上面 | ❌ 拒绝照抄。名字可以像，语义必须是我们自己的，否则等于把 dsh 的领域模型搬进 OpenSquad |
| **分发层** | bundle（npm 包内 `dsh.bundle.patch` → `cordis.patch.yml`）与 profile（`$DSH_HOME/profiles/<name>` 里有序的 `dsh.profile.bundles`）；`dsh plugin add` 走 pnpm，带 registry 回退（默认 npmmirror）与 GitHub `git ls-remote` 预检 | **最易移植**——纯约定 + 包管理 | ⚠️ 借鉴形态，v0 不引入 pnpm 依赖图；先做"目录即插件" |

**关键发现（原判断已更新）**：dsh **源码 vendor** 了 cordis（改名到 `@deepseek-ai/*` 作用域）并打上 **6 个本地补丁**。于是"宿主底座"从"从零写一个 JS 宿主"变成了"选一个现成依赖"。第 6 号补丁是 `cordis/src/fiber.ts` 的**可重入销毁加固**（effect 的 owner-list 包装必须在校验体之前注册；同步 setup 失败要移除包装并回滚；异步清理在静默前保持 owner 可见；owner 处于 `UNLOADING` 时拒绝新建 effect）——这份补丁列表要当**已知坑清单**读：若我们直接用上游 rc 版，得逐条核这些修复是否已进上游。

**对 mods 桥的含义**：mods 桥只是**一个 cordis 插件**。因此 mods 与 dsh 式插件共用同一个容器，不必维护两套宿主机——这是把两件事合并成一件的关键。

---

## 9. dsh 插件管理面 → Mods 页 v1 设计输入

Mods 页 v0 只做三件事：静态裁定（按 §3/§4 的矩阵离线算出 `runnable`/`partial`/`blocked`）、逐 mod 启停（只把意图写进 `data/mods/<name>/state.json`，绝不改 vendor 清单）、导入。dsh 的插件管理面（`packages/boot/plugin-manager/README.md`）提供了该抄与该弃的对照：

| dsh 的能力 | dsh 怎么做 | Mods 页 v1 | 理由 |
|---|---|---|---|
| 环境隔离 profile | `$DSH_HOME/profiles/<name>`，每个 profile 一份有序 bundles | ⚠️ 只抄形态 | 我们只有一个 workspace，v1 先给"一个 mods 根 + 一个状态目录"，不引入多环境 |
| 声明式装配 bundle | npm 包内 `dsh.bundle.patch` → `cordis.patch.yml` | ⚠️ 部分 | 我们的 mod 就是目录；但"**把 mod 的贡献列成可读清单**"值得抄——事件 / `$` / 插槽 / 命令 / 工具各挂了什么，摊开给用户看 |
| 逐行启停 | 每行 toggle | ✅ 已有（v0） | 只写意图，不动 vendor 清单 |
| 安装日志流 | 安装时流式输出 | ✅ v1 补 | 无沙箱 + 装即执行，用户必须看得见装了什么；我们现在的 import 是一次静默 `copytree` |
| 版本兼容闸门 + 豁免 | 拒绝不兼容版本，`compatibility.json` 给豁免 | ✅ v1 补 | 对应 §7 的离线闸门；豁免必须留出口，否则长尾 mod 会被一刀卡死 |
| 构建脚本审批 | pnpm 11 默认拒绝 postinstall，列 `pendingBuilds` 等用户批 | ⚠️ v1 记账 | v0 只收编译好的 `.js`（见 §6），没有构建步骤；等 M2 引入依赖图再谈 |
| 失败保留 + `error` | 装失败的 bundle 留在列表里并带 `error` | ✅ v1 补 | 与"加载时点名缺口"同源；现在 `blocked` 是扫描期算的，**装失败**是另一条路，v0 没覆盖 |
| 热更新（HMR） | 改插件即重载 | ❌ 推迟 | 在拿到 cordis 的 fiber 卸载语义之前，热更新只会漏资源 |

**一条设计原则**：dsh 管理面的价值不在"它有哪些按钮"，而在**每一个失败都留在界面里可追问**。v0 已做到"缺口点名"，v1 要把"安装/装载期的失败"也拉进同一口径。

---

## 10. 未决问题（阶段 1 动工前必须回答）

1. **JS 宿主形态 → 已定（原倾向升级为结论）**：宿主**以 cordis 为基座**（npm 依赖独立上游 `cordis@4.0.0-rc.7`），而不是自研 Node worker 或裸 Node 子进程——理由见 §8：它是现成依赖，且顺带补上 deny/改写、预算归属、可卸载注册三处。**剩下的决定**：①宿主跑在哪（渲染进程内 vs 独立 Node 子进程——仍倾向后者：`$.fs`/`$.process`/`$.http` **必须走 Python 侧闸门**，放渲染进程等于把权限交给前端）；②mods 桥成为 cordis 插件后，与现有 Python 插件机制的关系（并存两套注册面，还是让 cordis 侧只当"外来格式"的容器）。
2. **跨进程预算**：`tool.call` / `turn.complete` 一次往返可接受（1–10ms 级）；`prompt.edit` 的 50ms 已判 refused，但**要不要给 `prompt.submit` 设一个上限**（否则慢 hook 会拖住每轮对话）？
3. **`long_memory` 那类老问题**：加载过滤靠 `plugin.json` 的 `tools`/`hooks` 字段（`plugin_manager.py:244-266`），而 `self_learn` 的下划线命名 hook 被静默丢弃（`plugin_api.py:720`，`get_hook_methods()` 内）。mod 宿主上线前，**先把"声明与实际不一致"这类静默失败补上诊断**，否则调试成本会转嫁到 mod 作者身上。
4. **`$.session.messages()` 的读取路径与脱敏**（⚠️ 未核实）。
5. **TUI 是否 v0 就支持**（§5.6 提案：不支持）。

---

## 附：本矩阵的证据来源

| 事实面 | 来源 |
|---|---|
| mods 事件 / `$` / 元素 / 限额 / 契约 | `code.claude.com/docs/zh-CN/plugins/mods/reference`（v2.1.287 线） |
| 第三方宿主的差异处理与取舍 | `deepseek-ai/deepseek-harness`：`docs/subsystems/claude-code-mods.md`、`packages/experimental/claude-code-mods/src/{surfaces,elements,host-ops}.ts`（MIT） |
| cordis 框架语义 / vendored 补丁清单 | `github.com/cordiverse/cordis`（MIT，v4.0.0-rc.7）；dsh 的 `vendor/README.md`、`docs/cordis-primer.md`、`docs/cookbook/extension-cookbook.md` |
| dsh 插件管理面 | `deepseek-ai/deepseek-harness`：`packages/boot/plugin-manager/README.md`、`docs/user/develop/basic/publish.md` |
| marketplace 目录格式 | `code.claude.com/docs/en/plugins/create-marketplace` |
| OpenSquad hooks / 预算 / 链语义 | `src/opensquad/plugin_api.py`、`src/plugins/plugin_manager.py` |
| 工具注册与 level | `src/opensquad/registry.py` |
| 事件总线与事件词汇 | `src/opensquad/events.py`、`src/opensquad/_events/payloads.py` |
| 命令模型 | `src/opensquad/cli/slash_commands.py` |
| 插件视图通路 | `src/plugins/_sdk/index.ts`、`.../plugin-views/registry.ts` |
| pane 与 UI 落点候选 | `.../utils/paneViews.ts`、`.../components/`、`.../components/ai-chat/` |
| TUI | `src/opensquad/cli/tui/` |
