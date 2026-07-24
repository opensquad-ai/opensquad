# OpenSquad Launcher / Gateway — Rust 混合重构方案

> **受众**：维护者 / 架构决策  
> **状态**：草案（Draft）— 未开始实施  
> **相关**：[`contracts/launcher-http.md`](contracts/launcher-http.md)、[`contracts/ws-edge.md`](contracts/ws-edge.md)  
> **原则**：协议冻结、实现可替换；Python 继续做 Agent 大脑与插件；Rust 做可靠底座。

---

## 1. 目标与非目标

### 1.1 目标

1. 用 Rust 加固 **Launcher 进程监督** 与（后续）**Gateway 边缘代理**，提升常驻稳定性、跨平台进程控制与桌面分发体验。
2. **不破坏** 现有前端、Agent Runner、插件生态与 HTTP/WS 路径。
3. 全程可 **一键回退** 到纯 Python 实现。
4. 用契约测试保证「换实现不换行为」。

### 1.2 非目标

- 不用 Rust 重写 Agent Runner、`gateway_adapter`、插件 API、技能系统、MCP 业务语义。
- 不把 React/Electron 前端改成 Tauri（可作为远期独立议题）。
- 不在一期迁移 Gateway 群聊 DB、JWT 鉴权、上传、model-presets、插件市场审核。
- 不追求「全仓 Rust」；本方案是混合架构，不是语言替换运动。

### 1.3 成功判据（全局）

| 指标 | 通过线 |
|------|--------|
| API / WS 兼容 | 前端与 Agent **零改 URL / 零改帧类型名** |
| 功能回归 | `opensquad start` → Agent 起停、Web 对话、日志、重启、工作区切换通过 |
| 隧道 | 云 Gateway + 本地 Launcher `admin_request` 往返正常 |
| 稳定性 | 连续 24h：无僵尸 Agent、无泄漏旧 WS |
| 回退 | 环境变量 / 配置可切回 Python，≤ 1 分钟 |
| 收益 | 冷启动、崩溃恢复或桌面包体积至少一项可量化改善 |

---

## 2. 现状边界（事实基线）

```text
用户 / Web UI / Electron
        │
   Gateway :9555  (FastAPI)
   ├─ /api/*           群聊、鉴权、上传…（业务，留 Python）
   ├─ /api/ai-web/*    AI Web 管理与代理
   ├─ /api/launcher/*  → 透明代理到 Launcher :9600
   ├─ /ai-web/ws/*     User ↔ Agent 实时通道
   └─ /ai-ws/launcher  Launcher 管理隧道
        │
   Launcher :9600  (HTTP 管理 + 进程监督)
   ├─ spawn/stop Agent 子进程（Python runner）
   ├─ 插件服务进程
   └─ 配置 / FS / cards / MCP / workspace…
        │
   Agent 进程 × N  (Python) — LLM / 工具 / 插件
```

关键代码锚点：

| 模块 | 路径 |
|------|------|
| Launcher 入口 / 路由 | `src/opensquad/launcher_main.py` |
| 进程管理 | `src/opensquad/launcher/process_manager.py` |
| Gateway 入口 | `src/opensquad/gateway/backend/app/main.py` |
| WS 边缘 | `src/opensquad/gateway/backend/app/ai_web/websocket.py` |
| Agent→GW 适配 | `src/opensquad/gateway_adapter.py` |
| 系统配置 | `src/opensquad/system_config.py` |

---

## 3. 目标混合架构

```text
┌──────────────────── 保持不变 ─────────────────────┐
│ Agent Runner / plugins / skills / MCP / Textual   │
│ Gateway 业务：auth、群聊、uploads、presets…        │
│ Frontend：nexuschat-pro + Electron                 │
└───────────────────────────────────────────────────┘
                      │ 冻结契约
┌──────────────────── Rust 底座 ────────────────────┐
│ opensquad-launcher（P1）                           │
│   · 进程监督 start/stop/restart/logs               │
│   · HTTP :9600 核心子集                            │
│   · 其余路由 → Python sidecar :9601                │
│ opensquad-gateway-edge（P2，可选）                 │
│   · Agent/User/Launcher WS                         │
│   · /api/launcher 反向代理                         │
│   · nodes register                                 │
└───────────────────────────────────────────────────┘
```

### 3.1 P1 推荐进程形态（双轨）

```text
[opensquad-launcher :9600]          ← Rust（对外唯一入口）
   ├─ /api/ping, /api/agents, start|stop|restart, logs, stats
   ├─ /api/plugin-services/*/start|stop|restart
   ├─ /api/shutdown, /api/runtime/*
   └─ 其余 /api/*  ──反代──►  [opensquad-launcher-py :9601]
                               ← 现 launcher_main 裁剪版
```

前端与 Gateway **只看见 :9600**。

### 3.2 切换开关

| 变量 / 配置 | 含义 |
|-------------|------|
| `OPENSQUAD_LAUNCHER_IMPL=python` | 默认：现有 Python Launcher 独占 :9600 |
| `OPENSQUAD_LAUNCHER_IMPL=rust` | Rust 占 :9600，Python sidecar 占 :9601 |
| `OPENSQUAD_GATEWAY_EDGE_IMPL=python\|rust` | P2 用；默认 python |

桌面 / CLI `opensquad start` 读取该变量决定拉起哪个二进制。

---

## 4. 归属划分

| 归属 | 内容 |
|------|------|
| **Rust In（P1）** | 子进程生命周期、环境清洗、日志环形缓冲、核心管理 API、WS tunnel 客户端（可随 P1 或 P1.5） |
| **Rust In（P2）** | Agent/User/Launcher WS accept、连接表、心跳、帧转发；`/api/launcher/*` 代理；nodes 表 |
| **Python Sidecar** | config/role/MCP/cards/plugins/skills/fs/session-changes/workspace 等「文件与业务语义」API |
| **Python Stay** | Runner、插件装饰器、gateway_adapter、群聊 DB、JWT、TUI |
| **TS Stay** | nexuschat-pro 全部 |

---

## 5. 分阶段路线图

| 阶段 | 名称 | 预估 | 产出 |
|------|------|------|------|
| **P0** | 契约冻结 + 测试基线 | 1–2 周 | OpenAPI/WS schema + golden 测试 |
| **P1-spike** | Rust 最小可行 Launcher | 1–2 周 | ping + agents list + start/stop + sidecar 反代 |
| **P1** | Launcher Core 完整 | 4–8 周 | 进程核心全量 + 开关 + 文档 + CI |
| **P1.5** | 隧道与节点注册迁 Rust | 1–3 周 | `/ai-ws/launcher` 客户端 + register/heartbeat |
| **P2** | Gateway Edge | 6–10 周 | WS 边缘 + launcher 代理；与 Python GW 分流 |
| **P3** | 桌面常驻替换（可选） | 另议 | 减少 PyInstaller 常驻面 |

**硬规则：** P0 未完成不得开 P1 主开发；P1 未达成功判据不得开 P2。

---

## 6. P0 — 契约冻结（具体步骤）

### 步骤 P0.1 — 导出 HTTP 契约

1. 以 `launcher_main.py` 路由表为源，填写 [`contracts/launcher-http.md`](contracts/launcher-http.md)。
2. 对每条「P1 必保」路由记录：方法、路径、查询参数、请求/响应 JSON 示例、状态码、鉴权方式。
3. 用现网或 `httpx` 录制 1 次真实响应作为 golden（脱敏：去掉绝对路径中的用户名等）。

### 步骤 P0.2 — 导出 WS 契约

1. 填写 [`contracts/ws-edge.md`](contracts/ws-edge.md)：
   - Agent register / heartbeat / status
   - User chat WS 关键帧（类型名列表即可，业务 payload 标「透传」）
   - Launcher tunnel：`launcher_register` / `keepalive` / `admin_request` / `admin_response`
2. 用抓包或单测 fixture 固化示例 JSON。

### 步骤 P0.3 — 契约测试骨架

在 `tests/contracts/`（新建）加入：

| 测试 | 断言 |
|------|------|
| `test_launcher_ping` | `GET /api/ping` → `status=ok` |
| `test_launcher_agents_list_shape` | `GET /api/agents` 字段集合 ⊇ 基线 |
| `test_agent_register_frame` | 首帧非 register → 关闭；secret 错 → 401 语义 |
| `test_admin_request_roundtrip` | tunnel 上 GET `/api/ping` 往返 |

运行方式：对 **当前 Python** 实现跑绿，作为基线。之后 Rust 实现必须同测同绿。

### 步骤 P0.4 — 行为备忘（进程）

把下列「隐式行为」写进 `contracts/launcher-process.md`（P0 可先写在 HTTP 契约附录）：

1. 子进程入口命令与 cwd  
2. 环境变量白名单 / PyInstaller PATH 清洗  
3. stop 超时与强杀策略（含 Windows 进程树）  
4. 日志缓冲行数上限  
5. `register_to_gateway` 启停条件  

### 步骤 P0.5 — 评审门禁

- [ ] 契约文档 PR 合并  
- [ ] 契约测试在 CI（ci-fast）中对 Python 实现通过  
- [ ] 维护者确认「P1 必保路由」列表无遗漏前端调用  

**P0 完成定义：** 以上三项勾选。

---

## 7. P1-spike — 1–2 周验证（具体步骤）

> 目标：证明「Rust 对外 + Python sidecar」可行，而不是一次写完 Launcher。

### 步骤 S1 — 仓库布局

建议（可按 monorepo 习惯微调）：

```text
crates/
  opensquad-launcher/     # Rust 二进制 + lib
  opensquad-contracts/    # 可选：共享类型 / JSON Schema
scripts/
  run_launcher_rust.ps1 / .sh
  run_launcher_sidecar.py   # 或复用裁剪后的 launcher_main
```

### 步骤 S2 — Sidecar 最小改动

1. 给现有 Python Launcher 增加监听端口参数（默认 9600；sidecar 模式 9601）。  
2. Sidecar 模式：**不**再抢进程监督职责（或接受 Rust 为唯一 spawn 源，sidecar 只提供文件类 API）。  
   - Spike 阶段允许「Rust start/stop 调 Python 内部函数」或「Rust 自己 spawn，sidecar 只读状态文件」——二选一，文档里写死。  
   - **推荐 Spike：** Rust 自己 `spawn` Python agent 入口；sidecar 只服务非进程路由。

### 步骤 S3 — Rust 实现最小集

必须实现：

- `GET /api/ping`
- `GET /api/agents`
- `POST /api/agents/{id}/start`
- `POST /api/agents/{id}/stop`
- 其余路径：HTTP 反代到 `http://127.0.0.1:9601`

技术建议：`tokio` + `axum` + `serde_json` + `tracing`。

### 步骤 S4 — 开关接入

1. `OPENSQUAD_LAUNCHER_IMPL=rust` 时：`opensquad start` / 桌面启动脚本先起 sidecar:9601，再起 Rust:9600。  
2. 默认 `python`：行为与今天完全一致。

### 步骤 S5 — Spike 验收

- [ ] 契约测试在 `IMPL=rust` 下通过（至少 ping / agents / start / stop）  
- [ ] Web UI 能看到 Agent 列表并起停一个 Agent  
- [ ] 切回 `IMPL=python` 立即恢复  
- [ ] 输出书面结论：继续 P1 / 调整边界 / 放弃  

**Spike 失败则停止，不进入 P1 排期。**

---

## 8. P1 — Launcher Core 完整（具体步骤）

### 8.1 进程监督模块

按 `process_manager.py` 行为逐项移植：

| 步骤 | 任务 | 验收 |
|------|------|------|
| P1.1 | AgentProcess 结构：状态、pid、启动时间、重启计数 | 单元测试 |
| P1.2 | start：拼命令、cwd、env 清洗、管道采集 | 起真实 agent 冒烟 |
| P1.3 | stop / restart：优雅信号 + 超时强杀（Win/Linux/macOS） | 无僵尸进程 |
| P1.4 | 日志 ring buffer + `GET .../logs` | 与 Python 行数参数兼容 |
| P1.5 | PluginServiceProcess 启停 | UI 插件服务页可用 |
| P1.6 | `POST /api/shutdown`、runtime list/cleanup | CLI/桌面退出路径 |

### 8.2 HTTP 核心子集（Rust 实现，非反代）

迁入 Rust（与 [`contracts/launcher-http.md`](contracts/launcher-http.md)「P1 必保」一致）：

- `GET /api/ping`
- `GET /api/agents`、`.../stats`、`.../logs`
- `POST .../start|stop|restart`
- `GET /api/plugin-services` + start/stop/restart（及 logs 可 sidecar）
- `POST /api/shutdown`
- `GET /api/runtime/list`、`POST /api/runtime/cleanup`

### 8.3 继续 Sidecar 的路由

保持反代，不在 P1 重写语义：

- `/api/agents/*/config|role|mcp|working-directory|fs/*|model-card|role-prompt`
- `/api/plugins*`、`/api/skills*`、`/api/*-cards*`、`/api/mcp*`
- `/api/workspace*`、`/api/sessions*`
- `/api/resources/upload`、`/api/agents/create`（可视情况列入 P1 末尾）

### 8.4 配置与路径

| 步骤 | 任务 |
|------|------|
| P1.7 | 用 serde 读取 `system_config.json` 所需字段（launcher_token、ports、node_*、workspace） |
| P1.8 | 与 `syscfg` 路径约定对齐：workspace、agents 目录、logs 目录 |
| P1.9 | 桌面冻结环境：`OPENSQUAD_APP_DATA` / `OPENSQUAD_USER_DATA` 行为一致 |

### 8.5 工程化

| 步骤 | 任务 |
|------|------|
| P1.10 | CI：`cargo test` + 契约测试 job（matrix：python / rust） |
| P1.11 | 发布：Windows/macOS/Linux 产出 `opensquad-launcher` 二进制；桌面 extraResources 可选打包 |
| P1.12 | 运维文档：开关、端口、故障回退、日志位置 |
| P1.13 | 性能/稳定：本地 24h soak（起停循环 + 闲置） |

### 8.6 P1 完成定义

- [ ] `IMPL=rust` 为可选推荐路径；默认仍可为 python，直到维护者决定切默认  
- [ ] 契约测试全绿（python + rust）  
- [ ] 桌面/CLI 冒烟清单通过（见 §11）  
- [ ] 回退演练记录存档  

---

## 9. P1.5 — 节点注册与管理隧道

| 步骤 | 任务 |
|------|------|
| P1.5.1 | Rust 实现 HTTP：`POST /api/ai-web/nodes/register` 的**客户端**（Launcher→Gateway） |
| P1.5.2 | Rust 实现 WS 客户端：连接 `gateway_ws()/ai-ws/launcher`，发送 `launcher_register` |
| P1.5.3 | 收 `admin_request` → 打本地 `:9600`（注意：应打 Rust 自己，避免环）→ 回 `admin_response` |
| P1.5.4 | `keepalive` 12s 应用层心跳（对齐现逻辑） |
| P1.5.5 | 契约测试：`test_admin_request_roundtrip` 在 rust 下通过 |

完成后，Python sidecar **不再**负责 tunnel 线程。

---

## 10. P2 — Gateway Edge（具体步骤）

> 前置：P1（含 P1.5）稳定至少一次小版本发布。

### 10.1 分流形态

同机双进程（推荐先做）：

```text
:9555 由「入口」占用，二选一：
  A) Python Gateway 继续听 9555，把 /ai-ws/*、部分 WS 反代到 Rust Edge :9556
  B) Rust Edge 听 9555，把 /api/auth、/api/groups… 反代到 Python :9556
```

**推荐先做 A**（改动面小）：Python 仍是用户熟悉的主进程；Rust 只吃 WS 与高流量转发。

### 10.2 步骤清单

| 步骤 | 任务 | 说明 |
|------|------|------|
| P2.1 | crate `opensquad-gateway-edge` | axum + tokio-tungstenite |
| P2.2 | Agent WS：register / heartbeat / status / 消息环 | 连接表对齐 `registry.py` |
| P2.3 | 业务帧转发到现有 Python 处理或「透传给已注册逻辑」 | 避免重写 runner 事件语义；可 HTTP 回调 Python |
| P2.4 | User AI-Web WS：鉴权、多端连接、转发 | 历史加载可调 Python HTTP |
| P2.5 | Launcher tunnel **服务端** | 与 P1.5 客户端配对 |
| P2.6 | `/api/launcher/{path}` 代理 | 从 `main.py` 迁出 |
| P2.7 | `nodes/register` + `GET /nodes` | 内存表 + 可选落盘 |
| P2.8 | `OPENSQUAD_GATEWAY_EDGE_IMPL` 开关与 CI 矩阵 | |
| P2.9 | 24h soak + 多 Agent 并发流式 | |
| P2.10 | 文档与回退演练 | |

### 10.3 P2 明确不做

- `/api` 群聊、DM、搜索、上传业务实现  
- model-presets、插件市场 AST 审核  
- 会话历史存储语义迁移（继续 Python `agent_sessions`）

### 10.4 P2 完成定义

- [ ] 默认 python 边缘仍可用；rust 边缘通过契约 + E2E 冒烟  
- [ ] 云端隧道场景与本地同机场景均通过  
- [ ] 无前端改动即可切换  

---

## 11. 回归与冒烟清单

### 11.1 本地（每次 P1/P2 PR）

1. `OPENSQUAD_LAUNCHER_IMPL=python`：`opensquad start` 正常  
2. `=rust`：同样路径  
3. Web：Agent 列表、启动、对话一句、停止、再启动  
4. 查看 logs API / UI 日志面板有输出  
5. 工作区切换（sidecar 路径）不 502  
6. 插件服务起停（若启用 websearch 等）  

### 11.2 桌面（发版前）

遵循 `.cursor/rules/desktop-release-gate.mdc`：

1. 后端/launcher 构建产物就位  
2. `smoke_frozen_all`（若适用）  
3. Electron 启动无 “Backend did not start in time”  
4. 托盘与主窗口正常  

### 11.3 隧道（P1.5+）

1. Gateway 与 Launcher 分机或分端口模拟  
2. UI 经 admin 代理改 Agent config 成功  
3. 断开网线再恢复：tunnel 自动重连  

---

## 12. 回退方案

| 场景 | 动作 |
|------|------|
| Rust Launcher 异常 | 设 `OPENSQUAD_LAUNCHER_IMPL=python`，重启 start |
| Sidecar 无响应 | Rust 对反代返回 502；回退 python 独占 |
| Rust Edge 异常 | `OPENSQUAD_GATEWAY_EDGE_IMPL=python` |
| 发版回滚 | 上一版安装包不含 rust 二进制时，天然回退 |

禁止：在未提供开关的情况下删除 Python Launcher 入口。

---

## 13. 风险登记

| ID | 风险 | 等级 | 缓解 |
|----|------|------|------|
| R1 | Launcher 路由隐式行为多 | 高 | Sidecar + golden；禁止一次迁完 |
| R2 | Windows 进程树/杀软 | 高 | 对标现有 win 逻辑；集成测 |
| R3 | WS 帧语义漂移 | 中 | schema + 回放测试 |
| R4 | 桌面路径假设（PyInstaller） | 中 | 对齐 `backend-*` 布局或改 Electron 启动 |
| R5 | 人力被 Agent 功能开发挤占 | 中 | P0/P1-spike 小步；P2 单独立项 |
| R6 | 双实现漂移 | 中 | CI 双矩阵；默认实现变更需 RFC |

---

## 14. 文档与目录约定

```text
docs/
  rust-hybrid-refactor.md          # 本方案（主文档）
  contracts/
    launcher-http.md               # Launcher HTTP 契约
    ws-edge.md                     # WS / 隧道契约
    launcher-process.md            # （P0.4 产出）进程行为
crates/                            # （实施期创建）
  opensquad-launcher/
  opensquad-gateway-edge/          # P2
tests/contracts/                   # （P0.3 创建）
```

用户文档（`doc_cn` / `doc_en`）**暂不**写入 Rust 细节，直到 `IMPL=rust` 成为推荐默认；届时另开「高级 / 维护者」小节。

---

## 15. 决策记录（ADR 摘要）

| 决策 | 选择 | 理由 |
|------|------|------|
| D1 | 混合而非全量重写 | Agent/插件生态与迭代速度优先 |
| D2 | 先 Launcher 后 Gateway Edge | 边界更清晰、收益更直接 |
| D3 | Sidecar 反代文件类 API | 降低 P1 风险，避免重写 fs/cards 语义 |
| D4 | 环境变量双轨 | 可回退、可 A/B |
| D5 | P2 优先「Python 主端口 + Rust WS」 | 减少鉴权/DB 迁移 |

---

## 16. 实施检查清单（总表）

### P0

- [ ] `contracts/launcher-http.md` 填写完成  
- [ ] `contracts/ws-edge.md` 填写完成  
- [ ] `tests/contracts/` 基线测试对 Python 通过并进 CI  
- [ ] 进程隐式行为备忘完成  
- [ ] 评审合并  

### P1-spike

- [ ] crates 布局  
- [ ] sidecar 端口模式  
- [ ] Rust：ping / agents / start / stop + 反代  
- [ ] `OPENSQUAD_LAUNCHER_IMPL`  
- [ ] 验收结论文档  

### P1

- [ ] 进程监督完整  
- [ ] 核心 HTTP 子集迁入  
- [ ] 配置/workspace/桌面 env  
- [ ] CI 双矩阵  
- [ ] 24h soak  
- [ ] 回退演练  

### P1.5

- [ ] 节点 HTTP 客户端  
- [ ] WS tunnel 客户端  
- [ ] 契约测试通过  

### P2

- [ ] Edge crate + 分流  
- [ ] Agent/User/Launcher WS  
- [ ] launcher HTTP 代理  
- [ ] 开关 + soak + 回退  

### P3（可选）

- [ ] 桌面 extraResources 纳入 rust launcher  
- [ ] 评估缩小 PyInstaller 常驻范围  

---

## 17. 下一步（立即执行）

1. 评审并合并本方案与 `contracts/*` 骨架。  
2. 启动 **P0**：补全契约字段表 + 录制 golden + 契约测试进 CI。  
3. P0 门禁通过后，开 **P1-spike** 分支（建议名：`spike/rust-launcher`），严格两周时间盒。  

---

## 修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-07-23 | 0.1 | 初稿：混合架构、P0–P3 步骤、边界与检查清单 |
