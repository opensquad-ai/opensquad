# TRAE Agent 优化计划（最终共识版）

> 版本：1.0（2026-08-03，最终定稿）
> 性质：本文档为 TRAE agent 视角的最终执行计划，融合双方（v2.1 ↔ v3.1）共识，是对 `docs/优化修复方案_v2.md`、`docs/优化修复方案_v3.md` 的唯一实施依据。
> 范围：`opensquad web` 从 cmd 启动 → Web UI 可用 → 启动 agent305 → 创建第一个新会话 → 发起第一轮对话，全链路优化。

---

## 一、优化思路（核心方法论）

1. **先消除"等待"，再谈"提速"**：按对用户可感知的影响排序——A 段命令→UI（5-15s）> B 段 agent 就绪（2-4s）> C 段首轮 TTFT（一次性 1-3s + 模型 prefill）。
2. **只做已源码核实的项**：每个瓶颈都有对应源码行号 + 实测数据佐证，不做猜测性优化。
3. **懒加载必须配套预热**：0.8.6 已把工具模块 import 推迟到首轮，这个 trade-off 不补预热就会把成本转移给用户（首轮多等 1-3s）。
4. **不重复动已落地的优化**：lite/full 分级就绪、惰性路由、MCP 后台化、long_memory/jieba 线程化、工具 lazy import、首帧免 debounce、Claude 前缀缓存均已存在。
5. **每项独立提交、可裁剪**：15 项彼此无耦合，单点问题单点 revert。
6. **收益评估按实测量级**：经外部反馈修正，放弃"每轮 50-250ms 系统提示重建"等过时/高估项（实测 60KB str.replace <1ms，agent.md/skills/schema 均已缓存）。

---

## 二、测量基线（真实数据）

### 1. cmd → Web UI（A 段）
- Gateway 模块导入链累计约 1.5-2.5s（fastapi/httpx/sqlalchemy/app.*，见 `logs/importtime.log`）
- **`opensquad web` 默认启动 Vite 冷启动 5-15s** ← 当前最大单项等待（即使 `dist/index.html` 已存在）
- `_setup_local_mode` 每次无条件重写配置 + 跑 `update_workspace_config.py` 子进程 0.3-2s

### 2. 启动 agent305 → 就绪（B 段，`logs/agent305_restart.log`）
| 阶段 | 耗时 | 说明 |
|---|---|---|
| agent_runtime_ready | 359ms | 注册 gateway 只等这个 |
| builtin_tools_ready | 10ms | 22 个内置工具（filesystem eager，其余 lazy） |
| **plugins_ready** | **1791ms** | 插件发现+加载（12 个插件工具）← 就绪最大单项 |
| skills_ready | 39ms | 14 个技能 |
| agent_ready | 2862ms | 面向 UI 的"可对话" |
| long_memory / jieba | 3.2s / 1.8s | 已后台化，不阻塞 |

### 3. 首轮对话（C 段）
- 每次 LLM 调用固定输入约 **3 万 token**：sys≈12.5-15.4k + tool_defs≈15.5-17.8k（`base_fc.md` 33 个 include 展开约 60KB）→ 决定 prefill 首 token 延迟
- 首轮 `generate_openai_tools` 会触发 10 个 lazy 工具 import（**1-3s 一次性，在 TTFT 关键路径上**）
- 异常场景：`httpx` 单一 `timeout=120` 总超时，慢 prefill/挂死时"盲等"

---

## 三、已落地的优化（不再重复做）

| 机制 | 位置 |
|---|---|
| lite/full 分级就绪 + `/health/ready-lite` | `gateway/backend/app/main.py` |
| 惰性路由（_admin/_market 懒挂载）+ `_LazyImport` | `ai_web/routes/_main.py` |
| 重初始化任务全部 `create_task` 后台并行 | `main.py` lifespan |
| MCP 全后台化 + 健康检查 + 指数退避重连 | `tools/mcp_adapter.py` |
| long_memory / jieba 预热 `asyncio.to_thread` 后台化 | `agent_boot_phases.py`、`agents_boot.py` |
| 内置工具 10/11 lazy import | `agent_boot_phases.py` |
| 首帧免 30ms debounce 直发、已有 chunk 不重试 | `gateway_adapter.py`、`chat_api.py` |
| Claude `cache_control` 前缀缓存 | `claude_api.py` |
| agent.md / skills / tools schema / token 计数缓存 | `context_base.py`、`skill_loader.py`、`registry.py`、`chat_api.py` |
| `_kill_port_owners` 先探测后 netstat、`_find_python` 缓存 | `start_cmd.py` |

---

## 四、最终共识方案（15 项，分三批）

### 批次 1 — 低风险，纯启动路径，当天完成（预期 -6~19s + 首轮 -1~3s + 异常反馈 <30s）

| # | 优化项 | 改动点 | 预期收益 |
| --- | --- | --- | --- |
| 1 | **web 优先静态 UI**（A-1） | `web_cmd.py` run_web + `cli/main.py`：`dist/index.html` 存在即走 Gateway :9555，跳过 `_ensure_frontend`；新增 `--dev` 参数保留 Vite；Vite 已在跑时仍优先 Vite | 命令→UI **-5~15s** |
| 2 | **`_setup_local_mode` 幂等**（A-2） | `start_cmd.py` L419：src config 已是 `hosts.gateway=0.0.0.0` 跳过重写；workspace config 已生效跳过子进程；`.env.local` 内容一致跳过写 | 冷启动 **-0.3~2s**，二次≈0 |
| 3 | **lite 白名单扩充**（A-4） | `main.py` `_LITE_HTTP_PREFIXES` 加 `/api/groups`、`/api/ai-web/agents`（ready_lite 在 init_db 之后置位，DB 依赖安全） | 登录首屏 **-0.5~1.5s** |
| 4 | **MCP 连接硬超时**（B-4，防御兜底） | `mcp_adapter.py` `connect()`（L156）/`_connect_server`（L251）：每个 server 连接包 `asyncio.wait_for(timeout=max(5, cfg.timeout))`，超时走既有失败隔离+重连；`finally` 清理未注册 AsyncExitStack 防泄漏 | 单 server 挂死不拖 full_ready |
| 5 | **首轮工具 schema 后台预热** | `agents_boot.py`（agent_ready 后）+ `registry.py`：后台 `create_task` 调 `generate_openai_tools("all")` + `generate_tool_descriptions()` 填缓存；**参照 `_prewarm_tokenizer`（L654）范式** | 首轮 TTFT **-1~3s** |
| 6 | **httpx 分级超时**（C-6，微调①） | `chat_api.py` L51-62/L2024-2028：`httpx.Timeout(connect=10, read=120, write=30, pool=10)`；与"已有 chunk 不重试"配合 | 异常**秒级反馈**，不盲等 120s×6 |

### 批次 2 — 中风险，涉及并发/IO 语义，次日完成

| # | 优化项 | 改动点 | 预期收益 |
| --- | --- | --- | --- |
| 7 | **`setup_connections` 并行**（B-3） | `agent_boot_phases.py` L142-143：`_setup_web_server` 与 `_setup_gateway_adapter` 改 `asyncio.gather` 并发（无数据依赖；bridge 仍延后） | 注册提前 **0.3~0.6s** |
| 8 | **新会话归档异步化**（C-4） | `session_manager.py` L1097-1161：旧会话全量 JSON 归档改 `asyncio.to_thread`（保留"新空会话同步落盘"崩溃安全顺序） | 超大会话 New Chat 不卡事件循环 |
| 9 | **config.json 单次读取复用**（B-5） | `runner.py` L258 + `agents_boot.py`：`load_config` 结果 dict 传入 AgentRunner/model_switch 复用，去重 Pydantic 校验 | 启动 **-50~150ms** |
| 10 | **init_db 异步化**（A-3） | `main.py` L291-318 + `database.py`：`init_db()` 改 `create_task` + `_db_ready` Event；ReadinessMiddleware 对需 DB 路径等待 Event | 端口+ready 提前 **0.1~0.5s** |

### 批次 3 — 较大改动/涉及模型侧，单独评审

| # | 优化项 | 改动点 | 预期收益 |
| --- | --- | --- | --- |
| 11 | **插件加载移出事件循环**（B-1） | `agent_boot_phases.py` L449 + `plugin_manager.py` L431-472：`discover_and_load`/`register_tools_to_agent` 包 `asyncio.to_thread`（PluginManager 加锁）；plugin.json 落盘前比对内容，未变不写 | 就绪 **-1.5~2s**，启动期 UI 不卡 |
| 12 | **OpenAI 前缀缓存**（C-5） | `chat_api.py` L1890-1921：对支持 provider 显式注入前缀缓存参数；确认自动缓存命中 | 连续消息 TTFT 显著下降 |
| 13 | **clone 共享只读资源**（C-3） | `session_dispatcher.py` L23-91：新会话 ChatAPI 复用根实例 httpx client / tiktoken encoding | 新会话首个调用 **-50~500ms** |
| 14 | **主协程不 await extension_task**（B-2，微调②） | `agents_boot.py` L1107-1117：bridge/jieba 各自独立 task，监听 extensions_ready 事件再启动 | 主协程提前收敛 |
| 15 | **精简 base_fc.md**（C-1，最后 A/B） | `src/prompts/base_fc.md`：33 个 include 中低频规则移入按需 collab card / skill，常驻砍半 | 每轮 prefill **-1~5s** |

---

## 五、共识记录（两处分歧的最终裁定）

> 双方（v2.1 ↔ v3.1）经两轮讨论，13 项一致，2 项裁定如下。最终定稿 = v3.1 + 微调①②。

| 分歧 | 立场 | 裁定 |
| --- | --- | --- |
| **httpx 分级超时（C-6）批次归属** | v3.1 第一批 / v2.1 第二批 | **第一批 #6**。验证成本极低（不可达 IP 一测便知透传），收益独立（异常秒级反馈），早做无害。**微调①**：本地不可达 IP 验证 AsyncOpenAI 对 `httpx.Timeout` 透传生效；失效则回退为 `asyncio.wait_for` 首 chunk 方案 |
| **主协程不 await extension_task（B-2）是否保留** | v3.1 保留 / v2.1 删除 | **保留第三批 #14**。插件线程化后 extension_task 显著变快，await 成本降低，再解耦风险最小。**微调②**：须排在 #11（插件线程化）之后实施，保持"插件先于 bridge"顺序，规避 cancel-storm 历史风险 |

**补充采纳**：`_prewarm_tokenizer`（agents_boot.py L654）作 schema 预热参照范式；mcp_adapter `connect()` 与 `_connect_server` 双入口均列入改动点。

---

## 六、明确"不做"清单

- ❌ C-2 每轮字符串重建优化——实测 <1ms/轮，收益忽略级
- ❌ `_read_agent_md` / skills / tools schema 缓存——已存在
- ❌ 首帧 debounce / token 统计 / MCP 后台化 / 工具 lazy——0.8.6 已落地
- ❌ docker/npm/pypi 分发链路——用户已明确不管
- ❌ playwright MCP 保持禁用，不动 MCP 配置（#4 仅作防御性兜底）

---

## 七、验证与回滚

1. **语法检查**：`ruff check <改动文件>`（按项目配置）
2. **单测**：`pytest` 跑受影响模块；**基线 839 passed / 44 failed（44 为既有失败），零新增为准**
3. **实测基线对比**：`scripts/timed_chat.py` 等跑 before/after，记录：命令→UI 秒数、agent_ready 秒数、首 token 秒数
4. **回归场景**：①`opensquad web` 二次启动秒开；②新建会话→发消息正常收流；③MCP 异常时 agent 仍可对话；④登录/认证路径不受 #10 readiness 改动影响；⑤#6 不可达 IP 验证透传
5. **回滚**：每项独立提交，单点 revert

---

## 八、验收指标

| 场景 | 当前 | 批次 1 后 | 全批次后 |
| --- | --- | --- | --- |
| `opensquad web` → UI 可操作 | 5-15s（Vite） | **<3s**（静态 UI） | <3s |
| 首轮对话前固定成本 | schema import 1-3s + prefill | **schema 已预热** | + 提示词精简（#15） |
| 异常场景反馈 | 120s 盲等 | **<30s**（#6） | <30s |
| agent 完全就绪（含 MCP） | 视 MCP 健康 | 单点挂死 ≤8s 退出 | 同左 |

---

## 九、执行顺序

```
批次 1（当天）：1 → 2 → 3 → 4 → 5 → 6（含微调①）
批次 2（次日）：7 → 8 → 9 → 10
批次 3（排期）：11 → 12 → 13 → 14（微调②，须在 11 后）→ 15（base_fc，最后，A/B 验证）
```

每批完成后：全量回归（零新增失败）→ 实测基线对比 → 发布流程验证（切 release 分支 → CI 绿 → tag → GitHub Release）。

---

## 十、关键源码位置索引

- `web_cmd.py` L38 `_ensure_frontend`；L73 `_gateway_static_available`；L108 URL 选择
- `start_cmd.py` L419 `_setup_local_mode`
- `main.py` L192 `_LITE_HTTP_PREFIXES`；L291-318 lifespan `init_db`
- `uvicorn/server.py` L105 lifespan.startup → L172 create_server（lifespan 先于端口绑定）
- `mcp_adapter.py` L156 `connect()`；L251 `_connect_server`；L263 timeout 仅用于工具调用；L180-192 失败隔离
- `registry.py` L175 `generate_openai_tools` → `_ensure_module`（触发 lazy import）
- `tool_call_strategy.py` L207 `prepare_llm_call` → `generate_openai_tools`
- `chat_api.py` L62 `_make_llm_http_client`；L130 `timeout=120`
- `agents_boot.py` L654-672 `_prewarm_tokenizer`（预热范式）；L1107-1117 `await extension_task`
- `context_builder.py` L194 `_last_base_system_prompt_by_sid`
- `session_manager.py` L1097 `start_new_session`；`session_dispatcher.py` L23 `_clone_chat_api`
- `claude_api.py` L1080 `cache_control`（Claude 侧已有）
