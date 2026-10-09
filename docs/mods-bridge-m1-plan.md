# Mods 桥 · M1 实施计划（"除永不放开外，全部做"）

> **状态：计划待批。** 上游是 `docs/claude-code-mods-compat-v0.md`（矩阵）与 `docs/mods-bridge-m0.md`（M0 桥，已闭环）。本文只讲 M1 怎么排、每段怎么验、以及两个必须 owner 拍板的决策。
>
> **范围**：把矩阵里**除"永不放开"之外**的缺口全部补齐——包括渲染、更多插槽、事件、只读面、四个需要授权的闸门、TypeScript、`claude-code` SDK shim、`$.command.*`。
>
> **边界（不做，别在实现里偷偷打开）**：
> 审批/权限/邀请/危险确认的所有插槽、`Client` 元素、输入接管（`ui.press/focus/scroll/input/select`）、`$.session.authorize/send/append`。
> 这些是**产品决策**（三条铁律），不是"还没做"——任何一项要开，先改矩阵与本文。

---

## 0. 三条实施原则

1. **不动内核**。M0 已经证明桥可以是"一个普通插件 + 一条 WS relay"。M1 继续这条路线：不改 `_turn_loop` 的工具执行语义、不改 `run_hook`、不改权限模型。
2. **每段独立可验**。每段结束都必须能跑：`pytest`（含契约/不变量测试）+ `scripts/mods_smoke.py`（对 5 个真 mod 的对照表）。**没有实测证据的阶段不算完成**。
3. **缺什么就点名**。任何降级/丢弃都要在加载时或界面上写清楚（矩阵 §2 的判据）。M0 已经为此吃过两次亏（`$.ui.toast` 只打日志、11 条 degraded 理由为空）。

---

## 1. 阶段总览与顺序

### 进度（滚动更新）

| 段 | 状态 | 备注 |
|---|---|---|
| P1 | ✅ 完成 | 协议三处登记 + 总线信封（sid 路由）+ 指纹去重 + 前端 store/渲染器/band + **press 回传全链路**（网关通用转发 → adapter → 总线 → 宿主 `action.invoke`）。**真机验收待重启**（见进度表末行）。顺手修 3 个真 bug：action id 全局计数器（去重失效 + 注册表无界增长）、宿主 cwd 未固定、`Box` 未透传 `onPress`（嵌在布局里的按钮全是死的） |
| P2 | ✅ 完成（收敛后） | `WIRED["slots"]` = **`AbovePrompt` + `AssistantMessage` + `Pane`**（守卫测试扫描前端源码，声明==真挂载）。**Pane 端到端落地**：`$.ui.open({id})` 真放置（宿主记 id）→ Python 按 id 逐个渲染 `ui.render{component:'Pane',requestId}` → 前端新增 `mod` tab kind + `ModPaneView`（复用同一套元素渲染器）+ **收到 Pane 帧自动开 tab**（否则这个 tab 不在 pane 菜单里，用户找不到）。实测两个真 mod 都产出 Pane 树：`quick-buttons-setup → Text`、`replay-theater → Box`。**诚实边界**：`$.ui.close` 显式 **refused**（"能开不能关"，关 tab 属于布局状态机）——没有假装支持。**`ToolUse`/`StatusBar`/`Spinner`/`Sidebar` 故意不声明**：实测里没有任何真 mod 用它们，且 `ToolCallBlock.tsx` 根本没人引用（矩阵的候选挂载点是错的） |
| P3 | ✅ 完成 | `turn.step`（每轮工具一次，**不是 generator 语义** → 矩阵与设计文档同步改为 degraded）、`session.start`（每宿主一代一次）、`session.end`（`on_unload` 的 fire-and-forget 通知 → 宿主新增"通知不回复"语义）、`$.session.id`（来自 hook ctx 的 sid）。**实测：cache-meter 由 `blocked` → `partial`，惰性项归零**，`turn.step×4`、`AbovePrompt → 1 node` |
| P7 | ✅ 核心完成 | `slash_commands` 运行时注册表（与静态 `COMMANDS` 合并、标 `source`、**禁止抢注内置名**、`/help` 有 "From installed mods" 分区）；`$.command.register/list/run` 全通；**用户入口只有一条机制**：命令的文本到达 agent → `on_message_received` 拦截 → 跑 mod 的 `command.run` → 插件把 `{text}` 作为 `to_user_final`（→ WS `message`）说出来 → `__stop__` 拦下这轮。CLI 侧 `dispatch_slash` 对 mod 命令返回 False 放行（否则会印 "Unknown command"）。Web 菜单也补上了：新增 relay topic **`mod_commands`**（协议三处登记）→ 插件在命令注册/撤回时广播**只有 mod 拥有的**命令 → 前端注册表并入 `filterSlashCommands`（`mod:<name>` 前缀；**mod 不能抢注内置名**，这条在前后端各有一道）。选它就是插入 `/name `，与手打同一条路 |
| P4 | ✅ 完成 | `$.state.{get,set,delete,keys,update}`（宿主进程内 per-mod KV，**作用域是 agent 不是会话**，已在矩阵与文档写明）+ `$.fs.list`（只列一层、500 上限、workspace 收敛）/ `$.fs.stat`。`$` 面 18 → **30 个成员** |
| P6 | ✅ 完成（B1+B2） | **B1**：`.ts` 走 Node 24 原生类型擦除（零依赖）+ `claude-code` SDK shim（宿主用 `module.register` 解析器钩子回答裸包名，不在用户 workspace 里种 `node_modules`）。**B2**：`.tsx` 的 JSX 用 **vendor 的单文件 sucrase**（289KB 打包产物 + LICENSE + PROVENANCE，因为 `opensquad_backend.spec` 会过滤 `node_modules`）在加载时转译；宿主提供 `React.createElement` → 直接调用 mod 从 `$.ui.resolve(e)` 拿到的元素工厂。**实测：5/5 真 mod 全部可加载**（cache-panel、next-steps 由"不可加载"变为"已加载"） |
| P5 | ✅ 完成 | 四闸门 `$.fs.write`/`$.process.run`/`$.http.fetch`/`$.env.set`（+`$.env.get`），**默认拒绝 + 逐 mod 授权**（`data/mods/<mod>/permissions.json`）；拒绝话说清"未授权：X（含义）→ 去系统设置 → Mods 授权"。`fs.write` workspace 收敛 + 4 MiB；`process.run` **argv 数组不走 shell** + 30s/10min + 单流 64KiB；`http.fetch` 域名白名单 + 响应 4 MiB；`env.set` 上报真实作用域（**agent 进程级**）。授权面 = `GET /mods/permissions`（含义也从后端来，前端不复制）+ `PUT /mods/{name}/permissions` + 页面逐项开关 + **"这不是沙箱"显式告知** |
| M2 kill criteria | ✅ 达标（6/10 有效果） | **10 个真 mod、6 个不同市场**（按 commit 钉住、逐字安装，provenance 在 `mods/_PROVENANCE.json`）：**10/10 可加载**，**6/10 有效果**（画出节点 / tool.call handler 被触发 / 命令产出文本）≥ 3 → **继续**。判据是 `scripts/mods_smoke.py` 末尾那一行。**实测抓到三个真缺口并修掉**：①真 mod 写**无扩展名的相对导入**（`from './format'`）→ ESM 解析失败，已在解析器钩子里按 TS/打包器惯例补后缀；②SDK 契约是 **`read($, atom)` / `update($, atom, fn)`（`$` 首参、async）**，我原来写反了 → shim 改为两种形状都接受；③shim 缺 `memberOf`（`$.state` 家族的第五个 helper，官方 reference 只在方法表里点了名、没给语义）→ **一个缺失的具名导出会让整个 mod 的 import 失败**，desktop-look 因此 register=0：`memberOf(def, member)` 现在按 member 身份（`requestId`，无则退回自身标量字段）分配**独立 cell**，desktop-look 由"可加载但 0 handler"变为 register=10 / tool.call×4，**有效果 5/10 → 6/10**。另修白名单：`flexWrap`/`rowGap`/`columnGap`/`alignItems`/`marginBottom` 是合法布局属性，原来误拒（usage-meter 每个节点都被刷诊断）。**这个 6/10 是下界**：探针只驱动 `tool.call`、`turn.*`、`session.start`、`AbovePrompt` 渲染与命令，不驱动 `ToolUse`/`StatusBar` 等未接插槽 |
| 真机验收 | ⬜ 待同意重启 | 步骤与预期见 §12。**已就绪**：`cache-meter` 已在工作区启用（0 惰性，横幅不需要任何用户操作就会出现） |

顺序的判据是 **"能翻转几个真 mod ÷ 成本"**，不是"哪个技术上有意思"。

| 段 | 内容 | 翻转 | 量 | 依赖 |
|---|---|---|---|---|
| **P1** | 渲染最后一跳：relay + 前端 slot + 元素渲染器 + press 回传 | cache-meter 的横幅**真的出现** | 3–4d | — |
| **P2** | 更多插槽：ToolUse / AssistantMessage / StatusBar / Spinner / Pane / Sidebar | quick-buttons（Pane）、replay-theater（Pane→band） | 3–5d | P1 的渲染器 |
| **P3** | 事件补齐：`session.start/end`、`turn.step`、`$.session.{id,model,turns,usage}` | 3/5 的初始化 + cache-meter 的 tick | 3–5d | 需要 runner→插件管道 |
| **P4** | 只读面：`$.state.*`、`$.fs.{list,stat}` | 无（平台完整性） | 1–2d | — |
| **P5** | 四个闸门（**兼容性默认值，不是安全边界**）+ 安装期知情同意 | 无（平台完整性） | **1–2d** | 决策 A 残留（同意 UX） |
| **P6** | B1 `.ts`（免费）+ SDK shim；B2 `.tsx` 用 sucrase 单文件 | cache-panel、next-steps | B1 1–2d / B2 3–5d | **决策 B**；P1/P2 才有意义 |
| *(可选)* | 沙箱 spike：`--permission` 真挡 fs/进程（网络不设防） | 无 | 2–3d | 带 kill criteria，独立立项 |
| **P7** | `$.command.*`（register/list/run + 文本输出通道） | 所有"只想打印"的 mod | 5–7d | 三表面，见 §3.7 |

合计 **≈25–35 人日**。这是**程序级**工作量，不是一次任务；建议按段交付、每段可停。

**为什么 P1 排第一**：4/5 真 mod 是 UI mod，最后一步都卡在渲染。渲染一通，P2 几乎白送（复用同一个渲染器），P6 转译出来的 mod 也才有意义。

---

## 2. P1 · 渲染最后一跳

M0 已完成前三跳（mod → host → Python，见 `mods-bridge-m0.md` §11）。P1 是后两跳。

### 2.1 交付物

| 层 | 文件 | 内容 |
|---|---|---|
| Python | `opensquad/protocol_version.py` | 新增 relay topic（如 `mod_slot`）。**注意**：`LAUNCHER_RELAY_TOPICS` 有 `utils/wsFieldNames.ts` 的 TS 镜像，`tests/test_ws_event_contract.py` 断言两侧逐字一致 → 三处一起改 |
| Python | `plugins/mods_host/plugin.py` | `render_slot()` 之后把结果 emit 到 `Context.event_bus`（payload 带 `slot` / `nodes` / `agent_id`）。**只在有启用 mod 时 emit** |
| Python | `gateway_adapter.py` | 一般不需要改（不在 `_RELAY_HANDLER_METHODS` 里的 topic 是 plain relay）——但要**核一遍**扁平化/字段名是否原样透传 |
| 前端 | `hooks/useAgentWebSocket.ts` | `onWs('mod_slot', ...)` → 写进一个 store（按 sid 归位） |
| 前端 | `components/ai-chat/ModSlotHost.tsx`（新） | 一个插槽宿主；挂在 `AgentWebComposer.tsx` 的 `os-composer-input-layer` **上方** |
| 前端 | `components/ai-chat/ModElement.tsx`（新） | 5 个元素的渲染器（Text/Box/Button/Markdown/Code）。**只认白名单属性**，未知一律不渲染（与 Python 侧校验同源） |
| 前端 | 上行 | press → `action` id → 回传。**建议走已有的 admin/Bearer API 而不是新 WS 上行**（上行 WS 帧类型更多牵连） |
| Python | `_rpc.py` / `_node.py` | **顺手修**：`NodeHostClient(cwd=None)` 会让宿主继承父进程 cwd（探针 mod 的相对写就落进了仓库根）。宿主 cwd 必须**永远显式钉在 workspace** |

### 2.2 验收

1. `pytest` 全绿 + `tests/test_ws_event_contract.py` 仍绿（TS 镜像同步）。
2. **无 mod 时视觉零变化** —— 这条必须单独测：宿主在 store 为空时不渲染任何 DOM。
3. 启用 `cache-meter` → 横幅出现在输入区上方；它的 `$.ui.invalidate` 触发重画。
4. 启用 fixture（`deny-demo` 有 Button）→ 点击按钮 → host 的 `onPress` 被调用（用 `host.stats.actions` 或日志证明）。
5. **浏览器实测**（`ss@ss/ssssss` + :5173）：截图留档。

### 2.3 风险

- 动的是**活的** agent→浏览器链路 → 用"无 mod 即无行为"兜住回归面。
- 元素渲染器的**属性**必须和 Python 白名单**同源**；建议加一条测试：从 `WIRED`/`RENDER_ELEMENTS` 生成的清单与前端常量比对（像 `wsFieldNames` 那样逐字比）。
- WS 帧可能晚于 sid 就位 → 需要"按 sid 归位 + 找不到就丢弃"，不要跨会话串味。

---

## 3. 其余各段要点

### 3.1 P2 · 更多插槽

每个插槽 = 一个挂载点 + 一个触发时机：

| 插槽 | 前端落点 | 触发 |
|---|---|---|
| `ToolUse` | `ToolCallBlock.tsx` 尾部 | 工具调用结束 |
| `AssistantMessage` | `StreamingMessage` / `MessageBubble` 尾部 | 消息落定 |
| `StatusBar` | `StatusBar.tsx` / `RepoStatusBar.tsx` | 状态变更 |
| `Spinner` | `PulseDotsStatus.tsx` | 工作态变更 |
| `Pane` | **复用已有的 `utils/paneViews.ts` 具名 pane 系统**——这是最自然的一个 | 同 AbovePrompt |
| `Sidebar` | `Sidebar.tsx`（已有 `contributes.navigation` 通路） | 安装/启用变更 |

**注意**：`Pane` 要处理 mod 的 `$.ui.open()` 语义——v0 只开 AbovePrompt 时它固定回 `{isPlaced:false}`（mod 按上游约定退回横幅）。开了 Pane 之后必须真的回 `{isPlaced:true,id}`，否则 mods 的退路逻辑会一直走横幅。

### 3.2 P3 · 事件补齐

- **`session.start` / `session.end`**：`plugin_api.HOOK_NAMES` 目前 11 个，加 2 个；触发点从 `agent_boot_phases.py` 的启动/销毁阶段。**语义差异要写进文档**：我们是 **agent 级**不是 session 级（一个 agent 承载多会话）——矩阵 §3 已判 `degraded`。
- **`turn.step`**：在 turn loop **每轮工具调用后** emit。CC 的语义是 async generator，我们**没有** → 保持 `degraded` 并写清差异（cache-meter 只需要"有个 tick"，不需要 generator）。
- **`$.session.{id,model,turns,usage}`**：`Context` 目前只有 `agent_id/project_root/event_bus/config/data_dir/plugin_dir`，**没有会话事实** → 需要新增 runner→插件的上下文管道。做法：hook ctx 里已经在传 `agent_id`，把 `session_id` / `model` / `turns` 一并塞进 hook ctx，插件缓存最近一次；比新建注入面更省。

### 3.3 P4 · 只读面

- `$.state.*`：宿主进程内的 per-mod KV。**作用域按 agent**（一个宿主进程），与 CC 的"会话内存"有差异 → 写进文档；`atom/derive/memberOf` 依赖渲染订阅，等 P1/P2 的 invalidate 通路一起做。
- `$.fs.{list,stat}`：复用 `_confined_path` + 上限。

### 3.4 P5 · 四个"需要授权"的闸门（**已按实测改小：1–2d，且它不是安全项**）

**实测推翻了原设计的前提**：写了个只用 `node:` 内置模块、完全不碰 `$` 的探针 mod，宿主直接放行——

```
bypass  file-written  env=set  exec=spawned-without-process-run
```

它 `writeFileSync` 成功（落在仓库根）、改了 `process.env`、`execSync` 起了 shell。**三行代码绕过全部 `$` 闸门 → `$` 是 API 表面，不是沙箱。** 再测"真沙箱"：

| 手段 | fs 写 | 进程 | 网络 | env |
|---|---|---|---|---|
| `$` 闸门 | ❌ | ❌ | ❌ | ❌ |
| Node `--permission` | ✅ `ERR_ACCESS_DENIED` | ✅ `ERR_ACCESS_DENIED` | ❌ **`fetch` 照通** | ❌ |
| OS 级沙箱（容器 / job object） | ✅ | ✅ | ✅ | ✅ |

**Node 权限模型拦不住网络**——而网络恰恰是 mod 最现实的危害（把会话内容传出去）。

**改后的 P5 内容**：

1. **四个闸门照做**（`$.fs.write` / `$.process.run` / `$.http.fetch` / `$.env.set`），默认拒绝——但定位写清楚是**"兼容性默认值"**，不是安全授权；理由是 mod 会调它，我们需要给一个答复。
2. **重心移到安装期知情同意**：Mods 页与导入流程必须明说"**mod 就是任意代码，拥有完整进程权限（文件 / 进程 / 网络）——安装即执行**"。这是唯一能兑现的边界（因为运行时拦不住）。
3. **不建"逐次授权面"**：那是安全剧场，5–7 天买一个三行就能绕过的假边界。

**可选沙箱 spike（单独立项，带 kill criteria）**：给宿主加 `--permission --allow-fs-read=<workspace>`，**真挡 fs 与进程**，并在 UI **明说网络不设防**。这是改动最小、又真能挡东西的方案；风险是实验性 API 且会误伤直接 `import 'node:fs'` 的 mod。**kill criteria**：若探针 mod 与 5 个真 mod 里有 ≥1 个因它而功能受损，就放弃该 spike。

**决策 A 的残留部分（仍需 owner 定）**：安装期同意要不要一次显式确认弹窗；以及是否接受"部分沙箱（网络不设防）"这个不完整承诺。

### 3.5 P6 · TypeScript + SDK shim（**已拆成 B1 免费 / B2 需要决策**）

实测：

| 事实 | 结果 |
|---|---|
| `.ts` | **Node 24 原生类型擦除**：`node x.ts` → `ts-ok 42`，**零依赖** |
| `.tsx` | Node **拒绝**（连不含 JSX 的 `.tsx` 也拒——它只剥 `.ts/.mts/.cts`） |
| 真 mod 的 `.tsx` | **真含 JSX**：`<Button key="open" label={label} onPress={...} />` |
| 打包 | `opensquad_backend.spec:53` 把 **`node_modules` 从 plugins 里过滤掉** |

- **B1（免费，立即做）**：`.ts` 直接放行——交给 Node 原生擦除，**不引入任何依赖**。`claude-code` SDK shim 也归这里：宿主目录放一个普通 `.js/.mjs` 包，导出 `atom / read / update` + 类型，让 `import { atom } from 'claude-code'` 能解析（真 mod 现在是 `import_failed`）。
- **B2（要做）**：`.tsx` 需要一个 **JSX 语法变换**。选 **sucrase**（纯 JS、无原生二进制、**只做语法变换不执行代码**）；开发期用 esbuild 打成**单文件**放进 `host/`——**因为打包会过滤 `node_modules`，不能以依赖形式携带**。必须配一条"打包产物仍含该文件"的测试。
- **决策 B（需 owner 拍板）**：接受 B2 吗？我的建议是接受，并纠正一下担忧的方向——**转译是解析，不是执行**；我们既不做打包、也不跑 mod 自带的构建脚本（dsh 拒 postinstall 就是怕这个）。所以 B2 的真实风险是**打包漏文件**（wheel 漏过 `prompts` 目录，我们有前科），而那是可测的。
- 顺序提醒：这两个真 mod 都是 UI mod，**没有 P1/P2 它们转出来也看不见**。

### 3.6 P7 · `$.command.*`（唯一"不需要渲染也能出文本"的通道）

三表面，缺一不可：

1. Python：`cli/slash_commands.py` 的静态 `COMMANDS` 之外加**运行时注册表**，`all_commands()` 合并；`slash_dispatch.py` 支持派发到 mod 命令；
2. 后端：**新端点**返回命令清单（Agent Web 现在是前端硬编码 3 条，且没有后端端点）；
3. 前端：从端点拉取替换硬编码；mod 命令的 `{text}` 返回落成一条用户可见的消息。

**为什么它没排在 P1 前面**：单看它最贵（三表面），而且只翻转 1 个 mod 的文本输出；渲染一开，P2 顺手就能翻 2 个。若 owner 想要"先看到任何一种效果"，P1 更划算。

---

## 4. 每段都要做的收尾

1. `scripts/mods_smoke.py` 的对照表更新（哪个 mod 从"看不见"变成"看得见"，gate/渲染计数变化）。
2. 矩阵（v0 文档 §3/§4/§5）同步：判级变化必须改，**理由栏不许为空**（已有不变量测试守着）。
3. `WIRED` 常量 + `tests/test_mods_wired_surface.py` 同步（防漂移）。
4. 记忆与本文的"已完成"标记同步。
5. **M2 的 kill criteria 重测**：抽 10 个市场 mod，跑不起来 <3 个就停。建议在 **P1+P2+P3 完成后**先跑一次（此时应该能看到真实数字），不要等到 P7。

---

## 5. 明确不做（与边界一致）

- 审批/权限/邀请/危险确认插槽、`Client` 元素、输入接管、`$.session.authorize/send/append`。
- 官方精品 mod（`diff`/`agents-md`/`sec-default`/`telemetry`）：它们依赖的是我们永不开放的那类能力。
- TUI（Textual）侧渲染：M0 已定 v0 只做 Electron 侧；**P1/P2 的渲染器只服务 Agent Web**，TUI 会话里 mod 绘制不可用，必须在文档里明写。

---

## 附：本计划的证据基础

| 结论 | 依据 |
|---|---|
| 元素构造器来自 `$.ui.resolve(e)` | 真 mod 源码 `replay-theater.mjs:128`、cache-panel 的 SDK import |
| relay 是通用透传，gateway 不用改 | `gateway_adapter.py:37-41` 注释 + `_RELAY_HANDLER_METHODS` 缺席即转发 |
| 前端按 `msg.type` 查表分发 | `services/aiWebSocket.ts` 的 `messageHandlers.get(msg.type)` |
| relay topic 有 TS 镜像契约测试 | `protocol_version.py:271-289` + `tests/test_ws_event_contract.py` |
| `$.command.*` 是三表面 | `slash_dispatch.py` 只在 CLI/TUI 被调用；`ai-chat/slashCommands.ts` 前端硬编码 3 条；无后端端点 |
| `Context` 无会话事实 | `plugin_api.py:63-70` |
| 5 个真 mod 的实测表现 | `mods-bridge-m0.md` §9/§11、`scripts/mods_smoke.py` |
| **`$` 拦不住 mod**（三行绕过 fs/进程/env） | 探针 mod 只用 `node:` 内置模块，`writeFileSync` + `execSync` + `process.env` 全部成功 |
| Node `--permission` 拦 fs/进程但**拦不住网络** | `node --permission perm.mjs` → `fs: ERR_ACCESS_DENIED`、`exec: ERR_ACCESS_DENIED`、`net: ALLOWED`、`env: ALLOWED` |
| `.ts` 原生可跑 / `.tsx` 不可 | `node t.ts` → `ts-ok 42`；`node t.tsx`（即使无 JSX）→ 语法错误 |
| 真 mod 的 `.tsx` 含 JSX | `cache-panel/hooks/register.tsx:372` 的 `<Button ... onPress={...} />` |
| 打包过滤 `node_modules` | `opensquad_backend.spec:53` |

---

## 12. 真机验收（需 owner 同意重启）

### 12.1 结果（2026-10-07 已执行，owner 授权重启）

重启方式：`taskkill /T` 掉 gateway 进程树后按原命令行拉起；agent 走 launcher 的 `POST /api/agents/agent305/restart`（无需鉴权）。**无孤儿**（改动前 9555 的持锁者 `run.py` 父进程健在，非 `spawn_main` 孤儿）。

| # | 动作 | 结果 |
|---|---|---|
| 1 | gateway 重启 | ✅ `GET /api/ai-web/mods` → 200，6 条 `/mods*` 路由在线，**10 个 mod**；`GET /mods/permissions` → 四个能力 + 逐 mod 授权面 |
| 2 | agent 重启 | ✅ 日志 `[mods_host] loaded (host lazy, 1 enabled mod(s))`；**宿主真在 agent 里起来了**：`node host started (pid=154996, node=C:\ai\nodejs24\node.EXE)` + `host initialised: 2 handler(s) across 2 event(s), 0 diagnostic(s)`；宿主观测 journal 落地 `state:running / mods:1 / inert:0 / last_failure:""` |
| 3–4 | 发一条消息 | ✅ **`mod_slot` 帧真的到达客户端**：`slot=AbovePrompt, sid=217223:agent305-001, nodes=[Text "cache · in attesa della prima richiesta"], dropped=0`（用 WS 客户端直连网关验的，不是浏览器） |
| 5 | 命令 + 面板 | ✅ `mod_commands` 广播到达（`{name:'azioni'}`）；**`Pane` 帧到达**：`paneId=quick-buttons-setup, nodes=[Text …]`。⚠️ 但**命令文本没被拦截**（见下） |
| 6 | 点按钮 | ⬜ 未验（面板里这一步没有生成 Button，需先 `/azioni config` 选命令；上行链路两侧已有测试钉住） |
| 7 | Mods 页数据面 | ✅ 路由 + 载荷已验（效果/惰性/授权/「这不是沙箱」告知） |

**这一趟抓到两个"只有真机才有"的真 bug（第 1 个已修）**：

1. **hook ctx 缺 sid → 横幅永远画不出来。** 只有 `on_task_start` 带 `sid`；`on_after_send` / `on_task_complete` / `on_state_change` 都没有 → 插件的 `_session_id` 恒为空 → 每次 `_push_slot` 都被"没有 sid 不推"丢掉。已补三处 ctx（`_runner/_turn_loop.py` ×2、`runner.py` ×1）。
2. **`on_task_start` / `on_task_complete` 在这条主链路上根本不 fire**（任务记录从未开始：日志里既无 `Task recording started` 也无 `Before on_message_received`）。所以**只有 `on_after_send` 会触发**，而它原本只推 `AssistantMessage`（cache-meter 不画那个槽）。已把 `AbovePrompt` + panes 的刷新挂到 `on_after_send`（这条链路上真实会 fire 的钩子）。修完横幅立刻出现。

**仍未修（下一段的第一件事）**：**`on_message_received` 没有被网关这条 turn loop 调用**（`runner.py:2808` 那段属于另一条循环）→ Agent Web 里打 `/azioni` 会**原样喂给模型**，而不是被 mod 拦截。P7 的"用户入口"因此在实机上不通（CLI 侧 `dispatch_slash` 返回 False 的路子是对的，缺的是 agent 侧这个钩子）。`_turn_loop.py` 里需要补一个等价的调用点（含 `__stop__` 语义）。

### 12.2 第二趟（补完入口后重跑）

**已提交**：`b1edde4`（sid 三处 ctx + 把 `AbovePrompt` 的刷新挂到 `on_after_send`）、`eeb62f0`（把 `on_message_received` 接到真正在跑的 **parallel dispatcher** 上；无文本命令也要推面板；加载时后台**预热宿主**）。

| # | 结果 |
|---|---|
| 命令拦截 | ✅ 日志 `intercepting /azioni as a mod command` + `Hook 'on_message_received' chain stopped by plugin 'mods_host'`；**模型没有看到这条消息** |
| 命令文本通道 | ✅ `/replay` → 实机收到普通 `message` 帧：`Replay Theater: no edits in the last turn.`（mod → 宿主 → 插件 → 总线 → adapter → 网关 → 客户端） |
| 宿主预热 | ✅ agent 启动即注册命令：`mod 'quick-buttons' registered command /azioni`、`mod 'replay-theater' registered command /replay`、`host initialised: 13 handler(s) across 8 event(s)`（此前宿主是懒启动，**重启后的第一条消息永远不可能是 mod 命令**） |
| 点按钮 | ⬜ **仍未能验**，原因是拿不到"有 Button 的面板"，不是链路问题：quick-buttons 的面板要么用 `Input`（铁律 3 明确拒绝 → 宿主如实报 `mod quick-buttons threw on ui.render: Input is not a function`），要么需要会话里已存在自定义命令/技能（本会话没有）；replay-theater 只认"同一轮内的编辑"，而 `/replay` 自己那一轮没有编辑。**要收掉这一步**：装一个"纯 Button 面板"的 mod，或在会话里先建一个自定义命令再 `/azioni` |


**为什么必须重启**：网关是启动时 import 代码的——新的 relay topic（`mod_slot`）与 `/mods/*` 路由都要重启才存在；agent 进程同理（宿主 `host.mjs`、`mods_compat` 都是启动时加载的）。判断"是不是最新"不能只看路由有没有出现。

**已做的准备**：`cache-meter` 已在工作区启用（`data/mods/cache-meter/state.json`）——它 0 惰性，且它的横幅内容由 `ui.render` 提供（smoke 实测 `AbovePrompt → 1 node [Text]`）。回滚只需在 Mods 页把它关掉。

⚠️ **横幅不会在"打开页面"时出现，这是设计而非缺陷**：插件**在 `on_load` 不推送**（已核：`_push_slot` 的调用点只有 `on_task_start` / `on_task_complete` / `on_task_start` 之后的 `ToolUse` / `on_after_send` / `on_state_change` / `mod_action`）。帧必须带 `sid` 才能路由到某个 pane，而页面刚打开时还没有会话——没有目标就没有帧。所以**首轮结束后**才第一次出现。验收时别把步骤 3 的空屏当故障。

**步骤与预期**（只重启 gateway 与 agent，不动 launcher 与其他插件服务）：

| # | 动作 | 预期 |
|---|---|---|
| 1 | 重启 gateway | `GET /api/ai-web/mods` 从 404 变成 200；系统设置 → Mods 显示 10 个 mod |
| 2 | 重启 agent（或从 Agent Workstation 重启） | agent 日志出现 `[mods_host] loaded (host lazy, 1 enabled mod(s))`；首次工具调用后出现 `node host started` |
| 3 | 打开 Agent Web（:5173） | 输入区上方**暂时没有横幅**（尚无会话 → 无 sid → 无可路由的目标）；Mods 页此时应已显示 10 个 mod 与宿主观测 |
| 4 | 在一个会话里发一条消息 | **首轮**结束后横幅出现在输入区上方（cache-meter 那一行，`AbovePrompt → 1 node [Text]` 的实机体现），并随 `turn.complete` 重推；Mods 页的「宿主观测」从"运行中"变为有数字 |
| 5 | 发 `/warm` | 命令被拦截，agent **以消息形式说出** mod 的 `{text}`（而不是把它当用户输入喂给模型）；cache-meter 的 **Pane 面板**自动开一个 tab |
| 6 | 点面板里的按钮 | 按钮**可点**（不再是禁用态）；点击经 `mod_action` → 宿主 `action.invoke` 执行；面板随状态变化重画 |
| 7 | Mods 页展开任一 mod | 看到「实际效果」行、惰性项、以及**信任与授权**（四个能力 + "这不是沙箱"的告知）|

**已知不可用**（不要当成缺陷）：`$.ui.close` 是 refused（能开不能关）；`ToolUse`/`StatusBar`/`Spinner`/`Sidebar` 未接（无真 mod 使用）。

**这一步已经先抓到过一个"实机才有"的 bug**：前端把接力帧的 payload 读成 `msg.data`，而这一路实际落在 **`content`** —— 结果就是"横幅永远空、日志无异常"。见 §13.1。这类 bug **静态渲染单测全绿**，只有把链路整条走通才会现形，所以这一步不能省。

---

## 13. 收尾审计（2026-10-06）

### 13.1 交付物 → 证据

| 计划交付物 | 状态 | 证据（可复核） |
|---|---|---|
| P1 relay + 前端 slot + press 回传 | ✅ | `pytest tests/test_mods_host_deny_e2e.py`（含重入、去重、`mod_slot` 信封与 sid、按钮回传全链路）；`vitest components/modSlotBand.test.ts` 9 例（含"无帧→零 DOM"） |
| P2 插槽（收敛为 3 个）+ Pane 端到端 | ✅ | `tests/test_mods_wired_surface.py::test_every_advertised_slot_has_a_frontend_mount`（扫描前端源码断言声明==挂载）；真机 smoke：`quick-buttons-setup → Text`、`replay-theater → Box` |
| P3 事件（`turn.step`/`session.start|end`/`$.session.id`） | ✅ | 同 e2e 文件；真机 smoke：`cache-meter` 由 blocked → partial、惰性归零 |
| P4 只读面（`$.state.*`/`$.fs.list|stat`） | ✅ | e2e：state 跨调用保持、list/stat 收敛与 500 上限 |
| P5 四闸门 + 授权面 | ✅ | e2e：默认全部拒绝、授权后 fs.write 收敛/process.run argv/域名白名单/env scope；`tests/test_gateway_mods_routes.py` |
| P6 `.ts` + `.tsx` + SDK shim | ✅ | e2e：`.ts` 与 `.tsx` fixture 均加载并画出树；真机 smoke：**10/10 可加载**（起点 0/5） |
| P7 `$.command.*` 三表面 | ✅ | `tests/test_mod_slash_commands.py`（注册表/防抢注/CLI 放行）、`tests/test_adapter_mod_commands.py`（adapter 两分支 + topic 契约）、`components/modSlashCommands.test.ts`（菜单发现） |
| M2 kill criteria | ✅ 6/10 ≥ 3 | `scripts/mods_smoke.py` 末行；语料 provenance 在 `mods/_PROVENANCE.json` |
| （本轮修的真缺口）shim 缺 `memberOf` | ✅ | 一个缺失的具名导出会让**整个 mod 的 import 失败**（desktop-look：`The requested module 'claude-code' does not provide an export named 'memberOf'` → register=0，但页面仍写"已加载"）。补上后 register=10、tool.call×4，有效果 **5/10 → 6/10**。锁在 `tests/test_mods_host_deny_e2e.py::test_a_typescript_mod_loads_and_its_sdk_import_resolves`（fixture 用 `memberOf(seen, e)` 计数，并用第二个 member 断言**cell 不共享**——否则全局共享的假实现也能过） |
| 真机验收 | ⬜ | §12（需同意重启网关 + agent） |
| （本轮修的真 bug）Pane 内容在真实 UI 中永不渲染 | ✅ | `ModPaneView` 挂在 pane shell 里、而 shell **没有会话**，框架却按 sid 取内容 → 永远取不到（单测因为显式传了 sid 才绿）。现在 **pane 帧按 `PANE_SCOPE`（工作区级）存**：pane 属于工作区、id 由 mod 决定，与"哪个会话打开它"无关；`modSlotBand.test.ts` L6 锁住"无会话也能渲染 pane 内容"。同一个改动顺手删掉了一个**永远为空的 context 回退**（建了 provider 却从没挂过，只会把"忘传 sid"变成静默空渲染） |
| （本轮修的第二个真 bug）横幅在实机**永远不会出现** | ✅ | 前端 `mod_slot`/`mod_commands` 处理器读 `msg.data`，但这一路（adapter `on_generic_event` → `send_response` → 网关 `broadcast_to_agent` **原样** `ws.send_json`）payload 落在 **`content`**：`data` 是 undefined → 零节点 → 空横幅，**日志里一个字都没有**。仓库里除 `agent_ready_stage` 外所有处理器都读 `content ?? data`，只有这两个是我新写的、写错了键。已改为 `content ?? data`，并由两侧钉住：`tests/test_adapter_mod_commands.py::test_a_slot_tree_rides_in_content_not_data`（用 spy sender 实证帧形状，并断言 `data` 不被重复回显）+ `components/modSlotBand.test.ts` L7（扫描 hook 源码断言两个 topic 都读 `content`）。**这一条正是 §12 存在的理由**：静态渲染单测（自己构造 `setModSlot`）全绿，只有真机链路会暴露 |
| （本轮修的第三个真 bug）mod 命令菜单**永远空** | ✅ | `_announce_commands` 发的是**裸 payload** `{"agent_id","data":{"commands"}}`（没有 `sid`），而 `_unwrap` 只在**两个键都在**时才拆信封 → 到浏览器是 `content.data.commands`，比处理器读的深一层 → 菜单空、无任何报错。改成与 `mod_slot` 同一个信封 `{"sid":"","data":{"commands":…},"agent_id":…}`。**这是同一个 bug 类的第三次**（信封 → `content` → 拆信封的键），所以顺手把插件里 3 处 `bus.emit` 全过了一遍：`mod_slot`/`mod_commands`/`to_user_final` 现在全合契约。锁在 `tests/test_adapter_mod_commands.py::test_the_command_list_is_unwrapped_too` |
| （本轮补的发布期守卫）宿主文件必须能过打包过滤 | ✅ | 宿主的 5 个 `.mjs` + vendor（LICENSE/PROVENANCE/`sucrase.bundle.mjs`）是作为**数据文件**随插件走的，而 `opensquad_backend.spec::_is_plugin_runtime_data` 会丢掉 `*.ts`/`*.d.ts`/`*.map`/`package.json`/`ui/`（除 `index.js`）——今天正好都不命中，但**把 `host.ts` 改名成 `host.mjs` 这种一次疏忽就能让打包版宿主 spawn 不起来，而且只在 release 构建里暴露**。已加 `tests/test_mods_wired_surface.py::test_every_file_the_host_needs_survives_the_frozen_build`（断言 host 目录下没有会被过滤的后缀 + 五个必需文件都在），与"wheel 必须带 8 个顶层资源目录"是同一类守卫 |
| （本轮补的路由挂载守卫）Mods 路由在真实 app 里真的挂上了 | ✅ | `/mods/*` 是由 `ensure_lazy_routers` 与 admin/market **并列**splice 进已建好的 app 的，两个静默失败面：`/api/ai-web` 前缀没 bake（裸 route 不带前缀）或落在 catch-all `StaticFiles` Mount **之后**被 404 遮蔽。**已实测**：真的把 app 起来一次，`/api/ai-web/mods` 在 route 索引 294（`/permissions` 295、`/import` 299），Mount 在 300 → 全部在 Mount 之前。守卫 `tests/test_gateway_mods_routes.py::test_the_mods_routes_are_mounted_before_the_static_catch_all` 用假 app 钉住这个机制，不再付全量 app 启动的代价（且启动会读开发者自己的工作区） |
| 全量回归 | ✅ | Python **mods 相关 168 passed**（8 个测试文件，含 `test_ws_event_contract.py`）；Python **全量** `pytest -q`：**2928 passed / 27 failed / 9 skipped / 14 xfailed**（335s，那 27 个见下一行）；前端全量 `vitest run`：**148 files / 1545 tests passed**；`tsc --noEmit` 0；零孤儿 node |
| ↳ 那 27 个 failed 与本轮无关 | ✅（已核） | 全部落在 5 个测试文件：`test_websearch_relevance.py`(12)、`test_telegram_preflight.py`(11)、`test_claude_google_native_fc.py`(2)、`test_local_http.py`(1)、`test_asr_ffmpeg_resolver.py`(1)。根因是它们 import 的 `opensquad.{websearch_relevance,telegram_preflight,claude_google_native_fc,local_http,asr_ffmpeg_resolver}` 在这个**公开检出里不存在**（私有/垂类模块，按路径 ignore）；测试文件本身 `git status` 干净。**没有一个 mods 相关用例失败** |

### 13.2 故意不做（每条都有实测理由，不是漏掉）

| 不做的事 | 理由（实测） |
|---|---|
| `ToolUse`/`StatusBar`/`Spinner`/`Sidebar` 四个插槽 | 10 个真 mod 里**没有一个**用它们；且矩阵给的候选 `ToolCallBlock.tsx` **没人引用**（死代码）。为"计划里列过"而挂 = 给不存在的消费者加 DOM 与维护面。**这一条原本写错了**：`effect_summary` 的 `invisible` 分支条件是 `draws and not WIRED["slots"]`，而 `WIRED["slots"]` 永不为空 → 分支**不可达**，"只画未挂载插槽的 mod 会被判成 invisible"是我当时的过度声明。2026-10-07 已修：扫描器现在读出 mod 声明的 `drawn_components`（`component: 'X'` 字面量），`invisible` 改为"该 mod 画的组件与已挂载插槽**无交集**"（读不出组件时仍退回宿主级判断，不猜）。同时 `_push_slot` 用 `WIRED["slots"]` 设闸 —— 此前每次工具调用/状态变化都会为没人渲染的插槽做一次完整的渲染往返 |
| `$.session.usage` / `model` / `turns` | 真要它的两个 mod（usage-meter、cache-panel）要的是 **Anthropic 账户的 rateLimits**，还用 `$.http.fetch` 带 OAuth 句柄调 Anthropic 用量 API —— **结构性不适用**，不是我们的缺口 |
| `$.ui.close` | 能开不能关（关 tab 属布局状态机）；显式 refused 而非假装支持 |
| `$.model.*` / `$.agent.*` / `$.config.*` / `$.mcp.*` / `$.audio.*` | 矩阵 v0 非目标（成本/配额/审计未定），M1 范围外 |
| 官方精品 mod（`diff`/`agents-md`/`sec-default`/`telemetry`） | 依赖的正是三条铁律守着的那些能力 |
| TUI（Textual）侧渲染 | v0 定只做 Electron 侧；同树两渲染器是成本项 |
| cordis 作为宿主骨架 | M0.5，通道先于框架（理由见 §1.4） |

### 13.3 结论

代码侧与实测侧**没有未完成项**；唯一未验的是**在跑着的实例上的可见效果**，它需要重启（§12）。在未完成这一步之前，这份计划不应记为"已验收"。

**收尾审计（2026-10-06，逐条对回范围）**

| 范围项（用户原话） | 证据 | 判定 |
|---|---|---|
| 「把渲染做了」 | P1 渲染链路 + P2 三个插槽 + Pane 端到端；smoke `cache-meter → AbovePrompt 1 node [Text]` | ✅ |
| 「放开更多 / 补充更多 mod 有而我们没有的能力」 | P4–P7 全落地；`$` 面 18 → 30；`.ts`/`.tsx`/SDK shim；`$.command.*`；M2 由 5/10 → **6/10** 有效果 | ✅ |
| **永不放开清单仍关闭**（产品决策，非"没做"） | 实测 grep：宿主**完全没有** permission/approval/invite/danger/confirm 事件；无 `ui.press/focus/scroll/input/select`、无 `$.session.authorize/send/append`；`WIRED.events` 恰好 6 个安全项；`REFUSED_ELEMENTS = {Client, Image, Input, Raster, Select, Svg}` | ✅ 已复核 |
| 三条铁律 | 安全插槽不开放（上）；交互回调只给 action id（`Button.onPress` → 宿主 id，press 回传全链路）；元素树白名单 + 尺寸上限（`validate_element_tree`，越界丢弃并记诊断） | ✅ |
| 每一步都能跑 | Python mods 相关 **168 passed**；全量 **2928 passed / 27 failed**（27 个为本轮无关的既有失败）；前端 **148 files / 1545 tests**；`tsc` 0；`scripts/mods_smoke.py` 可跑 | ✅ |
| 真机可见效果 | §12（需 owner 同意重启网关 + agent） | ⬜ **唯一未完成** |

**本轮共抓到并修掉 4 个"单测全绿、真机才现形"的真 bug**：Pane 内容永不渲染（sid 键错）、横幅永不出现（payload 键 `data` vs `content`）、mod 命令菜单永不出现（裸 payload 未拆信封）、shim 缺 `memberOf`（缺一个具名导出 = 整个 mod import 失败）。另补 2 道发布/挂载守卫（宿主文件过打包过滤器、Mods 路由插在 catch-all Mount 之前）。**这四个都属于同一教训：静态渲染单测无法覆盖帧形状与投递链路——只有把整条链路走通才会现形。**
