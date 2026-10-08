# 协作窗口卡：卡片 kind 取舍方案

> 范围：群聊里的协作任务卡（`[[COLLAB_TASK]]` → `CollabTaskCard` → `CollabTaskWindow`）。
> 本文只做**决策与改动清单**，不改代码。相关检查结论见对话记录。

---

## 0. 一句话结论

线上协作卡只会是 `invite` 一种，这是**此前主动下线的结果**（提交 `52939b4`、`1c2f619`），不是漏接线。
因此推荐走**方案 A：收尾那次删除**（清掉残留写入方与死配色），而不是方案 B 反向复活分配/进度/完成卡。

唯一不管选哪条都该做的是**第 4 节**：让 `start_collaboration` 改走官方 encoder，消掉 marker 双路径。

---

## 1. 事实（逐条带证据）

**发射侧**

- 全仓唯一的 `[[COLLAB_TASK]]` 发射点是 `src/opensquad/tools/collaboration.py:344-349`，且只传 `kind="invite"`（`collaboration.py:338`）。
- 分配**有意不发卡**，代码里写明理由：`collaboration.py:1367-1371`
  「a task can be assigned many times, and each card was another '打开任务窗口' bubble in the group chat」。
  该决定由测试钉住：`tests/test_collab_task_card.py:44-55`（断言 `'kind="assign"' not in source`，同时要求 `[Task Assigned]` 仍在）。
- 任务内讨论/进度只写看板、不发卡：`collaboration.py:2570-2627`（docstring 明说读的地方是任务窗口）。
- 结束协作走任务窗口通知 + `status=done`，不发卡：`collaboration.py:780-801`。

**历史（为什么是"主动下线"）**

```
822ee6f feat(collab): collaboration-task card protocol and participant state   # 协议 + post_* 写入方诞生
5526ccc feat(collab): agents talk in a task-scoped channel
1c2f619 feat(collab): keep task talk out of the group chat                     # 原则：群聊保持干净
52939b4 feat(collab): assignments stop posting cards, an answered approval leaves the chat, ...  # 下线分配卡
```

即：`post_collab_task_card` 系列是**首版协议**的产物，`52939b4` 停用了它们的调用点，但**读取/编码侧的残留没清**。

**残留（写入方，全仓 zero refs）**

| 位置 | 现状 | 备注 |
|---|---|---|
| `collab_approval.py:718` `post_collab_task_card` | 全仓零引用（含测试） | 首版残留 |
| `collab_approval.py:748` `post_dm_collab_task_card` | 全仓零引用 | 首版残留 |
| `collab_approval.py:686` `patch_collab_task_status_in_content` | 仅测试引用 `test_collab_task_card.py:178-187` | 卡片 status 迁移路径线上不可达 |

**前端残留**

- `CollabTaskCard.tsx:47-53` 的 `KIND_CLASS`：`invite` 之外（`assign/progress/done`）永不被命中；`discussion` 仅作 fallback。
- `CollabTaskCard.tsx:122` 的 finished 判断含 `failed/archived`，而这两个状态全仓永不写入（只有 `done`/`stale` 会被设，`collab_board.py:342`）——无害，但属同类残留。

**仍然活着、必须保留的（重要，别误删）**

- `patch_collab_task_participant_in_content`（`collab_approval.py:696`）：邀请卡的 accept/decline 就地改写，走网关 `POST /groups/{groupId}/collab-tasks/{collabId}/respond`（前端 `services/api.ts:557`），是**活路径**。
- `encode_collab_task_message`（`collab_approval.py:608`）：把卡片 fallback 文案契约钉在测试里；**执行后已无生产调用方**，见 §8 待定项。
- `TASK_KIND_*` 常量与 `normalize_task_kind`（`collab_approval.py:503-544`）：解析端需保持宽容，`test_collab_task_card.py:58-63` 依赖 `normalize_task_kind("END") == "done"`；**删常量会破测试**。

---

## 2. 方案 A：收尾删除（推荐）

**理由**：与 `1c2f619`（群聊保持干净）和 `52939b4`（分配不发卡）确立的既定设计一致——群聊里只有"邀请卡 + @mention"，其余活动都发生在任务窗口与看板上。

**改动清单**

| # | 文件:行 | 动作 | 理由 |
|---|---|---|---|
| A1 | `collab_approval.py:718-745` | 删 `post_collab_task_card` | 零引用，首版残留 |
| A2 | `collab_approval.py:748-764` | 删 `post_dm_collab_task_card` | 零引用 |
| A3 | `collab_approval.py:686-693` | 删 `patch_collab_task_status_in_content` | 线上不可达 |
| A4 | `tests/test_collab_task_card.py:178-187` | 删 `test_patch_status_is_idempotent` | 随 A3 |
| A5 | `CollabTaskCard.tsx:47-53` | `KIND_CLASS` 只留 `invite`（+ 保留 `discussion` 作 fallback 或改用默认样式） | 死配色 |
| A6 | `collab_approval.py:517-523` | `_TASK_HEADLINES` 的 `assign/done` 条目：可选删除 | 仅经 encoder 可达；encoder 若只剩 invite 则它们也死。**纯清理项，可留** |

**保留不变**：`TASK_KIND_*` 常量、`normalize_task_kind`、`encode_collab_task_message`、`patch_collab_task_participant_in_content`。

**风险**：低。理论上从无 `assign`/`done` 卡被发出；即便历史数据里存在，前端 `parseCollabTask` 与后端 `normalize_task_kind` 仍是宽容解析，不会崩。

---

## 3. 方案 B：反向复活分配/进度/完成卡

**要做的改动**（供对照，不推荐）：在 `assign_task`（`collaboration.py:1355-1371`）、`post_task_message`（`:2598-2627`）、`end_collaboration`（`:780-801`）三处调用 `post_collab_task_card`（这本就是它存在的目的）。

**代价**

- **回归群聊噪音**：正是 `52939b4` 修掉的"每个分配一条『打开任务窗口』气泡"。
- **与既定原则冲突**：`1c2f619` 明确"keep task talk out of the group chat"；复活等于推翻两条已发布的决定。
- **要改测试**：`test_collab_task_card.py:44-55` 会被直接打破（它断言源码里没有 `kind="assign"`）。
- 交互上仍需处理：同一任务多次分配/多次进度 → 卡片刷屏，需要节流策略（现无）。

**只有在这种产品诉求下才值得**：明确要求"在群聊里就能看到进度推进/完成"。此时建议**只复活 `done`**（一条收口卡，不刷屏），进度仍留在任务窗口。

---

## 4. 正交项（两条路都该做）：`start_collaboration` 改走 encoder

**现状**：`collaboration.py:344-349` 手搓 marker，绕开 `encode_collab_task_message`（`collab_approval.py:608`），两边文案/字段会各自漂移。

**改法**：用 `encode_collab_task_message(payload)` 生成 marker 段，替换现手搓字符串。

**必须盯住的差异**（否则会悄悄丢掉 agent 依赖的文本）：
- 现 `invite_msg`（`collaboration.py:287-304`）以 `mentions_str`（`@成员`）**开头**，encoder 不含 @mention → 需保留为前缀拼在前面。
- 现文案含 `join_collaboration(...)` 指引与 group_id 说明，encoder 的 fallback 亦有 join 提示但措辞不同、且不含 group_id 分支说明 → 需逐行比对，确保 `Task ID:`、`Collab Card:`、`join_collaboration(...)`、`All board updates...collab_id=` 四行不丢。
- `tests/test_collab_task_card.py:170-175`（`test_encode_keeps_agent_readable_fallback`）已钉住 encoder 的关键行，可作为对照基线。

**收益**：消除 P2 双路径；以后改卡片文案只需改一处。

---

## 5. 决定与执行（2026-10-06）

按建议执行 **方案 A + 第 4 节**：

- **A1–A3 已做**：删除 `post_collab_task_card`、`post_dm_collab_task_card`、`patch_collab_task_status_in_content`（`collab_approval.py`，765 → 713 行）。`_rewrite_collab_task_marker` 保留（`patch_collab_task_participant_in_content` 仍在用）。
- **A4 已做**：删除 `tests/test_collab_task_card.py::test_patch_status_is_idempotent`。
- **A5 已做**：`CollabTaskCard.tsx` 的 `KIND_CLASS` 收窄为 `invite`，新增 `DEFAULT_KIND_CLASS` 作 fallback。
- **A6 未做**：`_TASK_HEADLINES` 的 `assign/done` 等条目保留——它们与仍被解析端接受的 `TASK_KIND_*` 常量同源，留着无害，清掉反而会让 `encode_*` 对合法 kind 退回通用标题。
- **第 4 节已做，但改法有偏差**，说明见 §8。

---

## 6. 其余检查项（2026-10-06 结项）

- **P3 — 已修。** `CollabTaskCard` 自持 `responding` / `responded`：点「参与」后在途禁用、成功后显示「已参与」且不可再点；`onRespond` 抛错则回到可点以便重试。锁在 `components/collabTaskCardRespond.dom.test.ts`（jsdom 真点击）。
- **P4 — 已修（只做与仓库约定一致的那一半）。** 按既有约定（`SessionSidebar` / `ProjectFilesPanel` / `GitRepoBar` / `AgentManagerPage`：定时器照走 + focus / `visibilitychange(visible)` 时立刻补一次），给 `CollabTaskCard` 与 `CollabTaskWindow` 补上该回调；卡片侧加了 `stopTimer`，避免唤醒时递归 `setTimeout` 分叉出第二条链。锁在 `components/collabPollAwake.scan.test.ts`。
  - 注意：这**不是**「隐藏时暂停轮询」，而是「回来时立刻刷新」——与全仓一致。若真要降后台负载，是另一个决策（会偏离现有约定）。
  - 仍**未做**：停止条件里的 `failed/archived`（装饰性，`done` 已在列）；卡片为拿 `participants` 打整个 `/summary`（需新增轻量 GET 路由，中等改动）。
- **P5 — 撤回，非缺陷。** 原判断有误：`_read_json` 底层 `json_io.read_json` 在 `JSONDecodeError` / `OSError` 时返回默认 `[]`（`json_io.py:29-35`），所以主文件损坏 / 截断 / 为空都会让 `if not current` 为真、回放照常触发；且 `_wal_replay` 另有 `_WAL_REPLAY_WINDOW` 年龄闸（`collab_board.py:206-231`）防「合法清空被旧快照覆盖」。无需改动。
- **P6 — 已修。** `doc_en/COLLAB_BOARD_DESIGN.md` 与 `doc_cn/COLLAB_BOARD_DESIGN.md` 按代码逐条核实后改：
  - ID 为 6 位**大写十六进制**（例 `A8F2C1`，非 `a8K2pQ`——`_gen_task_id` 用 `uuid4().hex[:6].upper()`）；
  - 状态集补 `failed | stale`（`collab_board.py:342`）；
  - `item_type` 列改为 `_SUMMARY_ITEM_TYPES` 的真实九项（去掉并不存在的 `progress`）；
  - 进度改为**清单标记派生**（`_derive_task_status_progress_from_content` + `update_task_progress`），删掉「PM 维护」；
  - §6 自动同步与 §10 后端 API 路径改为现状（`_runner/_tool_executor.py` / `_turn_loop.py`、`routes/` 包）；
  - §7 页面能力删掉并不存在的「按成员筛选」「PM 进度编辑」（`CollabBoardPage` 已核实无此二者），换成真实存在的「需求/方案文档就地编辑」「方案历史」。
  - 同时 `tests/test_collaboration_docs_match.py` 新增 3 条**代码联动**断言：文档状态集必须等于 `collab_board` 的校验元组、`item_type` 集必须等于 `_SUMMARY_ITEM_TYPES`、并禁用一批旧措辞。已验非空转（旧的 `active | done | archived` 会被判失败）。
  - 顺带更正一个"假漂移"：**存储路径文档原本没错**——`syscfg.workspace_data_dir("collab_board")` 就是 `<workspace>/data/collab_board`（`_syscfg/_workspace.py:71-73`），只把"工作区相对"写得更明确。

---

## 7. 决策后的验证方式

改完按下述命中面回验（本轮现状为全绿：Python 86 + 前端 39 通过）：

```bash
# Python
python -m pytest tests/test_collab_task_card.py tests/test_collab_card_participant_sync.py \
  tests/test_collab_remote_join.py tests/test_collab_gate_enforcement.py tests/test_collab_one_task.py \
  tests/test_window_cards.py tests/test_gateway_collab_task_window.py tests/test_gateway_collab_invite.py \
  tests/test_gateway_collab_task_respond.py tests/test_collaboration_end_to_end.py -q

# 前端
cd src/opensquad/gateway/nexuschat-pro
npx vitest run components/collabTaskWindow.test.ts components/collabTaskCardLive.scan.test.ts \
  components/ai-chat/collabTaskCard.test.ts components/ai-chat/windowCard.test.ts
```

若走方案 A：先确认 `grep -rn "post_collab_task_card\|post_dm_collab_task_card\|patch_collab_task_status_in_content" --include=*.py src tests` 为空。

---

## 8. 执行记录（2026-10-06）

### 第 4 节的实际改法（相对原方案的偏差）

原方案写的是「让 `start_collaboration` 调用 `encode_collab_task_message`」。实做时发现那样会**回归文本**：`encode_collab_task_message` 的 fallback 自带 `Task ID:` / `Collab Card:` / summary / `collab_id` 行，而现 `invite_msg`（`collaboration.py:287-304`）也有这些行，且额外带 `@成员`、`[Collaboration Started]`、中文 join 指引与 group_id 说明——直接替换会重复行、丢文本。

因此改成抽出更小的单一写点：

- 新增 `encode_collab_task_marker(payload)`（`collab_approval.py`），只产 marker；
- `encode_collab_task_message` 与 `_rewrite_collab_task_marker` 改为复用它（marker 的三处手写 JSON 收敛为一处）；
- `start_collaboration` 改用它替换原先手搓的 `f"{COLLAB_TASK_START}{json.dumps(...)}{COLLAB_TASK_END}"`，`invite_msg` 文本**一字未动**。

对 agent 可见的文本因此**零变化**：已断言 `encode_collab_task_message(payload).splitlines()[0] == encode_collab_task_marker(payload)`，且 marker 可被 `parse_collab_task_payload` 原样读回。

### 验证（全绿）

- `pytest`（13 个协作相关文件，含 `test_collaboration_docs_match.py`）：**115 passed**
- `vitest`（`collabTaskWindow` / `collabTaskCardLive.scan` / `ai-chat/collabTaskCard` / `ai-chat/windowCard`）：**39 passed**
- `tsc --noEmit`（gateway `tsconfig.json`）：exit 0
- `ruff check`（三个改动文件）：All checks passed
- `grep` 确认 `post_collab_task_card|post_dm_collab_task_card|patch_collab_task_status_in_content` 在 src/tests 已归零

### 执行中新暴露的待定项

`encode_collab_task_message` 在 A1/A2 之后**也没有生产调用方了**（只剩 3 个测试把它当 fixture：`test_collab_task_card.py:17`、`test_gateway_collab_task_respond.py:59`、`test_window_cards.py:135`）。它与我这次删掉的写入方同属"完整卡片消息编码器"，但语义上仍有价值（是唯一把 fallback 文案契约写下来的地方，且被测试钉死）。两条路：

- **保留（现状）**：作为测试 fixture + 文案契约说明，并在注释里注明"无生产调用方"；
- **或一并删除**：把 3 处测试改用 `encode_collab_task_marker` + 手写 fallback，同步删掉 `test_encode_keeps_agent_readable_fallback`。

没有生产路径依赖它，故留待你定。
