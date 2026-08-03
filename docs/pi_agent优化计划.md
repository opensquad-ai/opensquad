# OpenSquad 启动→首轮对话 优化计划（最终定稿）

> 版本：v1.0 最终定稿（2026-08-03）
> 状态：**共识达成，仅存档、暂不实施**（代码保持原始状态）
> 定位：本文件为**唯一实施基线**。此前 `docs/优化修复方案_v2.md`（v2.1）与 `docs/优化修复方案_v3.md`（v3.2）因两方各自更新产生漂移，本文件收敛二者，以"第二轮共识记录"的裁定为准。

---

## 一、共识历程与最终裁定

### 历程

1. 原始性能报告（`docs/启动到首轮对话性能优化报告.md`）→ 逐条源码核实（12/14 属实，2 项修正）
2. 方案 v2.1（13 项）与 v3.1（15 项）同源，核心内容一一对应
3. 第二轮共识讨论：v2.1 对 3 个分歧点**主动让渡**，裁定记录追加于 v3.md 尾部
4. v3.2 更新时引用了 v2.1 旧文档结构（13 项），与共识记录产生三处矛盾
5. **本文件裁定：以共识记录为准（15 项）**，理由：

| 分歧点 | 最终裁定 | 理由 |
| --- | --- | --- |
| httpx 分级超时（C-6） | **第一批**（附透传验证微调） | 验证成本极低（本地不可达 IP 一测即知），收益独立（异常秒级反馈），早做无害 |
| config.json 复用（B-5） | **保留（第二批）** | 收益 -50~150ms 虽小但零风险，"不做"省不了事却丢确定性收益 |
| 不 await extension_task（B-2） | **保留（第三批，排插件线程化之后）** | 插件线程化后 await 成本显著降低，再解耦风险最小；保持"插件先于 bridge"顺序规避 cancel-storm |

### 最终结构：15 项 = 批次1（6）+ 批次2（4）+ 批次3（5）

---

## 二、测量基线（真实数据）

### 1. cmd → Web UI（A 段）

- Gateway 模块导入链累计约 1.5-2.5s（fastapi/httpx/sqlalchemy/app.*，见 `logs/importtime.log`）
- **`opensquad web` 默认启动 Vite 冷启动 5-15s** ← 当前最大单项等待（即使 `dist/index.html` 已存在）
- `_setup_local_mode` 每次无条件重写配置 + 跑 `update_workspace_config.py` 子进程 0.3-2s

### 2. 启动 agent305 → 就绪（B 段，`logs/agent305_restart.log`）

| 阶段 | 耗时 | 说明 |
| --- | --- | --- |
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

### 4. 优化后实测（2026-08-03，15 项全部实施后）

| 场景 | 实测值 | 对比基线 |
| --- | --- | --- |
| agent305 重启 → 首轮对话完成（launcher 触发重启计起） | **12.7s**（重启 2s + 登录 0.3s + WS 握手 0.05s + TTFT 6.4s） | 旧版首轮 8s + agent_ready 2.9s 系统开销已不再叠加 |
| 首轮 TTFT（agent 就绪 + schema 已预热） | **6.4s**（纯模型 prefill/传输，deepseek-v4-flash 固有延迟） | 旧版 TTFT 含首轮 schema import 1-3s |
| 重启后 WS 可连接握手 | **45-51ms** | 旧版未测 |
| 微调①验证（httpx 分级超时） | 不可达 IP **22s** 触发 APIConnectionError | 原 120s 盲等（-5.4 倍） |
| AsyncOpenAI 重试叠加 | `max_retries=0`，dead endpoint 反馈 **22s** | SDK 默认重试 2 次 → 65.8s |

**结论**：插件线程化、setup_connections 并行、schema 预热、extension_task 解耦已生效——启动期系统开销（插件 1.8s、agent_ready 2.9s、schema import 1-3s）全部移出用户感知路径，首轮 TTFT 已收敛为纯模型时间；异常反馈从 120s 降至 22s。

### 5. 冷启动验证（2026-08-03，`opensquad stop` → `opensquad web --no-browser`）

| 场景 | 实测值 | 验证点 |
| --- | --- | --- |
| `opensquad stop` 全量停止 | **7.5s**（26 进程 + 9555 释放） | — |
| `opensquad web` 冷启动 → 输出 URL | **7.4s** | #1 静态 UI 优先、#2 幂等跳过 |
| 冷启动后 Vite（5173） | **未启动** ✅ | #1 生效：`[web] Using Gateway static UI at http://127.0.0.1:9555/` |
| 冷启动后 gateway 就绪 | **立即 `ready_lite=true, ready=true`** | #10 init_db 后台化 + #3 lite 白名单 |
| 静态 UI 可访问 | 200 / 9805 字节 HTML | 9555 直出 |
| 冷启动后 agent305 | **自动恢复**（alive=true, health_ok=true） | auto_start_on_boot |
| 冷启动后首轮 TTFT | **5.4s**（热启动 6.4s） | 无系统开销叠加 |

**冷启动链路结论**：命令 → UI 可操作从旧版 **5-15s（Vite）** 降至 **7.4s 全链路**（含 gateway+launcher+registry 从零启动）；且 5173 不再启动、服务冷启后立即 ready。若进一步只测"gateway 已运行时的二次 `opensquad web`"（#2 幂等跳过后），耗时已收敛至秒级。

---

## 三、批次 1 — 低风险，纯启动路径（当天，预计 -6~19s + 首轮 -1~3s）

| # | 优化项 | 改动点 | 预期收益 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | **web 优先静态 UI**（A-1） | `web_cmd.py` run_web、`cli/main.py`：`dist/index.html` 存在时直接走 Gateway :9555，跳过 `_ensure_frontend`；新增 `--dev` 参数保留 Vite 开发模式；Vite 已在跑时仍优先 Vite | 命令→UI **-5~15s** | 低 |
| 2 | **`_setup_local_mode` 幂等**（A-2） | `start_cmd.py` L419：① src config `hosts.gateway` 已是 `0.0.0.0` 跳过重写；② workspace config 已生效跳过 `update_workspace_config.py` 子进程；③ `.env.local` 内容一致跳过写 | 冷启动 **-0.3~2s**，二次≈0 | 低 |
| 3 | **lite 白名单扩充**（A-4） | `main.py` `_LITE_HTTP_PREFIXES` 加 `/api/groups`、`/api/ai-web/agents` | 登录首屏 **-0.5~1.5s** | 低 |
| 4 | **MCP 连接硬超时**（B-4，防御性兜底） | `tools/mcp_adapter.py` `connect()`（L156）/`_connect_server`（L251）：连接 task 包 `asyncio.wait_for(timeout=max(5, cfg.timeout))`，超时走既有失败隔离 + 后台重连；**`finally` 清理未注册 AsyncExitStack 防子进程泄漏** | 单 server 挂死不再无限拖 full_ready | 低 |
| 5 | **首轮工具 schema 预热** | `agents_boot.py`（agent_ready 后，参照 `_prewarm_tokenizer` L654 范式）`create_task` 后台调 `generate_openai_tools("all")` + `generate_tool_descriptions()` | 首轮 TTFT **-1~3s** | 低 |
| 6 | **httpx 分级超时**（C-6） | `chat_api.py` `_make_llm_http_client`（L62）：`httpx.Timeout(connect=10, read=120, write=30, pool=10)`，与"已有 chunk 不重试"配合 | 异常**秒级反馈**，不再 120s 盲等 | 低~中 |
| | **微调 ①** | #6 验收条件：本地不可达 IP 验证 AsyncOpenAI 对 `httpx.Timeout` 透传；**失效则回退 `asyncio.wait_for` 首 chunk 方案** | | |

## 四、批次 2 — 中风险，并发/IO 语义（次日）

| # | 优化项 | 改动点 | 预期收益 | 风险 |
| --- | --- | --- | --- | --- |
| 7 | **`setup_connections` 并行**（B-3） | `agent_boot_phases.py` L142-143：`_setup_web_server` 与 `_setup_gateway_adapter` 改 `asyncio.gather`（无数据依赖；bridge 仍延后） | gateway 注册提前 **0.3~0.6s** | 中 |
| 8 | **新会话归档异步化**（C-4） | `session_manager.py` L1097-1161：旧会话 JSON 归档改 `asyncio.to_thread`（保留"新空会话同步落盘"崩溃安全顺序 + `_drain_pending_mutations_sync` 语义） | 超大会话 New Chat 不卡 | 中 |
| 9 | **config.json 单次读取复用**（B-5） | `runner.py` L258、`agents_boot.py`：`load_config` 结果 dict 传入 AgentRunner/model_switch 复用，去重 Pydantic 校验 | 启动 **-50~150ms** | 低 |
| 10 | **init_db 后台化**（A-3） | `main.py` L291-318：`init_db()` 改 `create_task` + `_db_ready = asyncio.Event()`；ReadinessMiddleware 对需 DB 路径等待 Event；`_app_ready_lite` 提前 | 端口+ready 提前 0.1~0.5s | 中 |

## 五、批次 3 — 较大改动/模型侧，单独评审

> 顺序理由：**#12 前缀缓存先于 #15 精简**——前缀缓存依赖 system 消息字节稳定；先建缓存后精简，即使 A/B 不通过缓存收益仍在；反向则精简会让缓存失效、需重建。高风险项（#15 base_fc 精简）置于批次末尾。

| # | 优化项 | 改动点 | 预期收益 | 风险 |
| --- | --- | --- | --- | --- |
| 11 | **插件加载移出事件循环**（B-1） | `agent_boot_phases.py` L449、`plugin_manager.py` L431-472：`discover_and_load`/`register_tools_to_agent` 包 `asyncio.to_thread`（PluginManager 加锁，先评估共享状态）；plugin.json 落盘前比对内容未变不写 | 就绪 **-1.5~2s**，启动期 UI 不卡 | 中~高（逐个插件回归） |
| 12 | **OpenAI 前缀缓存**（C-5） | `chat_api.py` L1890-1921：对支持 provider（DeepSeek 等）注入前缀缓存参数；至少确认自动缓存命中 | 连续消息 TTFT 显著下降 | 中（配置开关 + 逐个 provider 验证） |
| 13 | **clone 共享只读资源**（C-3） | `session_dispatcher.py` L23-91：复用根实例 httpx client / tiktoken encoding（只读，请求状态隔离） | 新会话首个调用 **-50~500ms** | 中（并发压测） |
| 14 | **主协程不 await extension_task**（B-2） | `agents_boot.py` L1107-1117：bridge/jieba 独立 task，监听 extensions_ready 事件再启动 | 主协程提前收敛 | 中~高 |
| | **微调 ②** | #14 **须排在 #11（插件线程化）之后**实施，保持"插件先于 bridge"顺序 | | |
| 15 | **精简 base_fc.md**（C-1） | `src/prompts/base_fc.md`：33 个 include 中低频规则移入按需 collab card / skill，常驻砍到一半 | 每轮 prefill **-1~5s** | **高**（人工审模板 + A/B） |

## 六、验证与回滚（每批合入前必须执行）

1. **语法**：`ruff check <改动文件>`
2. **单测**：`pytest` 受影响模块；**基线 839 passed / 44 failed（44 为既有失败），零新增为准**
3. **实测对比**：`scripts/timed_chat.py` before/after，记录命令→UI 秒数、agent_ready 秒数、首 token 秒数
4. **回归场景**：① `opensquad web` 二次启动秒开；② 新建会话→发消息正常收流；③ MCP 异常时 agent 仍可对话；④ 登录/认证路径不受 readiness 改动影响（#10）
5. **回滚**：每项独立提交，单点问题单点 revert

## 七、明确"不做"清单

- ❌ C-2 每轮字符串重建优化——实测 <1ms/轮，收益忽略级
- ❌ `_read_agent_md` / skills / tools schema 缓存——已存在
- ❌ 首帧 debounce / token 统计 / MCP 后台化 / 工具 lazy——0.8.6 已落地
- ❌ docker / npm / pypi 分发链路——用户已明确不管
- ❌ playwright MCP 保持禁用，不动 MCP 配置（#4 仅防御性兜底）

## 八、验收总指标

| 场景 | 当前 | 批次 1 后 | 全批次后 |
| --- | --- | --- | --- |
| `opensquad web` → UI 可操作 | 5-15s（Vite） | **<3s** | <3s |
| 首轮对话前固定成本 | schema import 1-3s + prefill | **schema 已预热** | + 提示词精简（#15） |
| 异常场景反馈 | 120s 盲等 | **<30s**（#6） | <30s |
| agent 完全就绪（含 MCP） | 视 MCP 健康 | 单点挂死 ≤8s 退出 | 同左 |

## 九、执行顺序

```
批次 1（当天）：1 → 2 → 3 → 4 → 5 → 6（含微调①）
批次 2（次日）：7 → 8 → 9 → 10
批次 3（排期）：11 → 12 → 13 → 14（微调②，须在 11 后）→ 15（最后，A/B）
```

每批完成后：全量回归（零新增失败）→ 实测基线对比 → 发布流程验证（release 分支 → CI 绿 → tag → GitHub Release）。

## 十、关键源码位置索引

- `web_cmd.py` L38 `_ensure_frontend`；L73 `_gateway_static_available`；L108 URL 选择
- `start_cmd.py` L419 `_setup_local_mode`
- `main.py` L192 `_LITE_HTTP_PREFIXES`；L291-318 lifespan `init_db`
- uvicorn `server.py` L105 lifespan.startup → L172 create_server（.venv 与 frozen 一致）
- `mcp_adapter.py` L156 `connect()`；L251 `_connect_server`；L263 timeout 仅工具调用用；L180-192 失败隔离
- `registry.py` L175 `generate_openai_tools` → `_ensure_module`（触发 lazy import）
- `tool_call_strategy.py` L207 `prepare_llm_call` → `generate_openai_tools`
- `chat_api.py` L62 `_make_llm_http_client`；L130 `timeout=120`；L2274 被动读 cached_tokens
- `agents_boot.py` L654-672 `_prewarm_tokenizer`（预热范式）；L1107-1117 extension_task await
- `context_builder.py` L194 `_last_base_system_prompt_by_sid`（变化检测）
- `session_manager.py` L1097 `start_new_session` 同步归档
- `session_dispatcher.py` L23 `_clone_chat_api`
- `claude_api.py` L1080 `cache_control`（Claude 侧已有）
