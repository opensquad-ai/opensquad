# 会话 Token 超限 400 报错 —— 独立审查报告

> 审查对象：ss@ss / agent305 / 会话 `20260803_151735_a7s7`
> 报错：`400 invalid_request_error`，DeepSeek 侧 "requested 1064547 tokens (1064547 in the messages, 0 in the completion)"，上限 1,048,576
> 审查日期：2026-08-04

---

## 一、你的诊断报告复核结论


| 诊断项                                    | 结论            | 证据                                                                                 |
| -------------------------------------- | ------------- | ---------------------------------------------------------------------------------- |
| 会话 245 条消息（user2/assistant122/tool121） | ✅ 属实          | 实测消息数 245，角色分布一致                                                                   |
| 总字符 143 万、tool 结果占 99.6%               | ✅ 属实          | 实测 1,438,444 字符，tool 占 1,433,069（99.6%）                                            |
| 单条最大 tool 结果 48,627 字符（read_file）      | ✅ 属实          | filesystem__read_file 48,627                                                       |
| latest_summary 空、archived=0（从未压缩）      | ✅ 属实          | 均确认                                                                                |
| cl100k 计数约 36 万（3.97 字符/token）         | ✅ 属实          | 实测 cl100k=362,141 / o200k=363,706                                                  |
| 日志 used=405,867 / thought 异常           | ✅ 属实（含新增发现）   | launcher.log 23:37:35 记录                                                           |
| **"DeepSeek tokenizer 密度高 2.94 倍"归因**  | ❌ **不成立，需修正** | DeepSeek-V3 官方 tokenizer 实测仅 385,403 tokens（3.73 字符/token），与 cl100k 几乎一致（比值 1.06x） |
| 压缩阈值永不触发的机制                            | ✅ 属实          | `_prepare_messages`：cl100k 406K < token_max×0.75=750K                              |
| 修复系数 0.75（1.33 字符/token）               | ⚠️ 方向对，系数不可靠  | 见第四节                                                                               |


---

## 二、核心问题回答：为什么本地/中转都显示 ~40 万，模型却报 106 万？

**一句话**：本地（406K）与中转（433K）用的是**同一类粗粒度低估口径**（cl100k 家族），而模型服务端统计到 **1,064,547** —— 两者之间存在 **~2.6 倍系统性偏差**，导致本地压缩阈值按错误标尺计算，判定"安全"（40.6%）而实际已超限（106 万 > 104.8 万）。

**三方的"标尺"实测（同一份 143 万字符内容）**：


| 统计方                           | Token 数       | 字符/token | 口径                                                     |
| ----------------------------- | ------------- | -------- | ------------------------------------------------------ |
| OpenSquad 本地（`_count_tokens`） | 405,867       | 3.97     | tiktoken cl100k_base（`_build_encoding` 对未知模型回退 cl100k） |
| 中转 OpenCode Go usage          | 433,178       | —        | 与 cl100k 同类（比本地高 ~7%，疑含消息结构/元数据计数）                     |
| DeepSeek-V3 官方 tokenizer      | 385,403       | 3.73     | 与 cl100k 几乎一致（1.06x）                                   |
| 字节级 BPE（r50k_base）            | 724,222       | 1.99     | tiktoken 中最细                                           |
| **模型报错权威值**                   | **1,064,547** | **1.35** | DeepSeek 服务端                                           |


**关键推论**：模型报错的 1.35 字符/token，**比所有已知 tokenizer 都细 47% 以上（含字节级 r50k）**。因此 106 万不能简单归因于"DeepSeek tokenizer 天然更密"——常规 BPE 达不到这个密度。真相必然是以下之一（或两者叠加）：

- **可能 A**：deepseek-v4-pro 使用了近似"字符级/digit 级"的极端细粒度 tokenizer（把数字逐个拆、代码切得更碎）。
- **可能 B（更可疑）**：**模型侧统计的 messages ≠ 本地 req 的 143 万字符**。中转/模型服务端在转换 OpenAI 兼容请求时对内容做了扩展（重新序列化、注入结构、展开工具结果等），使模型侧解码的文本显著大于本地发送的 content。

> 无论 A 还是 B，对修复的影响一致：**不能依赖本地 cl100k 计数判断是否超限**，必须保守估算 + 发送前硬上限保护 + 源头截断工具输出。只改本地计数（即使系数完全准确）也堵不住可能 B 的漏洞。

---

## 三、代码层确认的根因链

```
1. chat_api.py _build_encoding()
   → encoding_for_model("deepseek-v4-pro") 抛 KeyError
   → 回退 tiktoken cl100k_base          ← 低估标尺 ①

2. _count_tokens() 遍历 message.items()
   → 只处理 content / tool_calls 字段
   → reasoning_content 字段被忽略      ← 低估标尺 ②（次要）

3. _prepare_messages()
   → current_tokens = get_current_token_count()   # cl100k 口径
   → threshold = token_max × ctx_trigger_threshold (0.75) = 750,000
   → 406K <= 750K → 不压缩，原样发出   ← 压缩永不触发

4. 请求带 143 万字符 messages 到达中转 → 中转转发 DeepSeek
   → DeepSeek 服务端真实解码 = 1,064,547 > 1,048,576
   → 400 invalid_request_error        ← 用户可见报错
```

---

## 四、对你修复方案的审查

```python
if "deepseek" in (self.base_url or "").lower() or "opencode" in (self.base_url or "").lower():
    content_tokens = int(len(value) * 0.75)   # 1.33 字符/token
```

**优点**：方向正确（识别 deepseek 系需独立口径）；实现简单零依赖；系数与本会话实测（1.351）接近。

**问题 1 —— 系数不可迁移（最大风险）**：

- 系数 0.75 由**单一内容特征**（99.7% ASCII 代码/JSON）拟合得出。
- 中文内容：DeepSeek 中文约 1 token/字 → `len×0.75` **低估 25%**（仍可能漏判超限）。
- 英文 prose：约 3–4 字符/token → `len×0.75` **高估 3 倍**（正常会话被过早压缩，误伤）。
- 建议改为保守取大：`content_tokens = max(int(len(value) * 0.8), len(self.encoding.encode(value)))`，或按内容类型分段估算后取 max。

**问题 2 —— base_url 判断误伤**：

- `opencode.ai` 代理的**不一定是 deepseek 模型**（agent301 就是 opencode + qwen3.6-plus）。仅按 base_url 分支会让 qwen 等模型也套用 DeepSeek 系数。
- 建议**同时检查 model_name**：`"deepseek" in self.model.lower() or "opencode" in base_url and "deepseek" in model`。

**问题 3 —— 只改计数不防"可能 B"**：

- 若模型侧存在请求扩展（第二节可能 B），本地系数再准也还原不了模型侧真实值。
- **必须叠加发送前硬上限保护**：估算（保守口径）> token_max×0.85 时强制压缩/截断，绝不把超限请求发出去。

**问题 4 —— 未触及膨胀根源**：

- 121 条 tool 消息 143 万字符、单条 48K，才是本会话膨胀根源。
- 即使计数修复，一次 `read_file` 输出 100 万字符也能瞬间超限。**根治靠工具输出截断**（read_file 默认截断到 6–8K 字符，大文件用 tail/sed 或分段读取）。

---

## 五、完整修复建议（按优先级）


| 优先级    | 措施                                                       | 位置                                              | 说明               |
| ------ | -------------------------------------------------------- | ----------------------------------------------- | ---------------- |
| **P0** | 发送前硬上限保护：估算 > token_max×0.85 强制压缩/截断                     | `chat_api._prepare_messages`                    | **必须做**，堵住所有口径偏差 |
| **P0** | 工具输出截断：read_file 等默认 ≤8K 字符                              | `tools/filesystem` 或 `add_tool_result`          | 根治膨胀源            |
| **P1** | 修正 deepseek 系计数口径：按 model_name 分支 + 保守系数（取 max）          | `chat_api._count_tokens`                        | 让压缩阈值按真实尺度生效     |
| **P1** | `_count_tokens` 纳入 `reasoning_content` 字段                | `chat_api._count_tokens`                        | 修低估标尺 ②          |
| **P1** | 压缩触发后用 summary 替换超大 tool 结果                              | 已有 `_prepare_messages` 压缩逻辑                     | 让 0.75 阈值真正工作    |
| **P2** | 修复 `thought` 统计异常（日志 23:37:35 thought=636,840 > used 本身） | `token_breakdown` / `_restore_cumulative_stats` | 显示误导             |
| **P2** | 中转 usage 不可信：UI 显示本地估算并标注"估算值"                           | 前端 token 面板                                     | 避免误信中转           |


---

## 六、验证清单（对原报告第六节的独立复核）

1. **会话字符数** ✅ 实测 1,438,444（你报 1,438,908，差 464，量级一致，口径差异）
2. **1064547 = 1438908 ÷ 1.35** ✅ 算术成立（1438444 ÷ 1064547 = 1.3512）
3. **压缩阈值逻辑** ✅ 确认 `token_max×0.75 = 750K`，cl100k 计数 406K 永不触发
4. **系数 0.75 评估** ⚠️ 对当前场景近似，但对中文低估 / 英文高估，不可迁移；建议取 max 保守策略
5. **非 deepseek 影响** ✅ 按 base_url 分支不影响纯非 opencode 模型，但会误伤 opencode 上代理的其他模型（qwen 等）→ 需结合 model_name

**新增发现（原报告未覆盖）**：

- DeepSeek-V3 官方 tokenizer 实测 3.73 字符/token，与 cl100k 一致（1.06x）——**"2.94 倍密度差"并非常规 tokenizer 差异**。
- 所有已知 tokenizer（含字节级 r50k=1.99）都无法还原 1.35 的密度，指向"模型侧请求扩展"或"v4 极端细粒度 tokenizer"。
- 日志 `thought=636,840` 异常（超过 used=405,867）暴露 token_breakdown/累计统计 bug，虽不直接导致 400，但会误导面板。

---

## 七、结论

你的诊断**方向正确、数据可靠**，但**核心归因（DeepSeek tokenizer 密度高 2.94 倍）经实测不成立**。真实情况是：本地与中转共用粗粒度低估口径（cl100k 家族），而模型服务端统计（1.35 字符/token）比所有已知 tokenizer 都细，二者 ~2.6 倍系统性偏差导致压缩阈值失效。

**修复必须三管齐下**：① 发送前硬上限保护（防一切口径偏差）；② 工具输出源头截断（消除膨胀源）；③ 修正 deepseek 系本地计数（让压缩阈值按真实尺度触发）。仅改计数系数（`len×0.75`）不足以根治。

---

## 八、补充实测（2026-08-04 二次验证，决定性证据）

利用 DeepSeek 官方 tokenizer 本地代码（`C:\ai_save\codex_work\deepseek_v3_tokenizer`）做了完整复现，**推翻所有"tokenizer 密度"假设**：

### 8.1 按 DeepSeek 官方 tokenizer + chat_template 完整渲染实测

DeepSeek-V3 tokenizer 是 **LlamaTokenizerFast（SentencePiece 风格，词表 128K）**，其 chat_template 会把消息包装成 `<｜User｜>...`、`<｜tool▁calls▁begin｜>...`、`<｜tool▁output▁begin｜>...` 等特殊格式。用 `AutoTokenizer.apply_chat_template` 渲染完整 245 条消息后编码：


| 复现场景                   | 字符            | Tokens        | 字符/token | vs 模型报错      |
| ---------------------- | ------------- | ------------- | -------- | ------------ |
| 原始 content 拼接          | 1,438,444     | 385,403       | 3.73     | 2.76x 差距     |
| **chat_template 完整渲染** | **1,473,607** | **347,082**   | **4.25** | **3.07x 差距** |
| str(dict) 拼接           | 1,578,007     | 386,893       | 4.08     | 2.75x        |
| ASCII 转义 JSON          | 1,626,657     | 415,221       | 3.92     | 2.56x        |
| content 双重转义           | 1,963,602     | 533,570       | 3.68     | 1.99x        |
| **模型报错权威值**            | —             | **1,064,547** | **1.35** | —            |


### 8.2 决定性结论

**本地无论用哪种 tokenizer（cl100k / DeepSeek-V3 官方 / chat_template 完整渲染），都只能还原 35–42 万 tokens；模型侧统计的 106 万是本地 req 的约 3 倍（需 3.07 倍膨胀）。**

这个 ~3 倍膨胀**无法在本地任何序列化/转义假设下复现**，**必然发生在中转 OpenCode Go → DeepSeek 的转发环节**。最可能的中转行为（数字上吻合 3 倍）：重复发送消息、将消息与系统提示/工具定义重复注入、或对 tool 结果做大幅展开。

### 8.3 对修复方案的最终裁决

- ❌ **用 DeepSeek 官方 tokenizer 替换本地计数不可行**：实测其 chat_template 渲染密度（4.25 字符/token）比 cl100k 更稀疏，用它计数**反而更低估**（347K vs 406K），压缩更不会触发。
- ❌ `**len×0.75` 系数修复也不可靠**：系数来自本会话拟合，且模型侧存在 3 倍转发膨胀，本地系数再准也还原不了真实值。
- ✅ **唯一可靠的修复组合**（按重要性）：
  1. **发送前硬上限保护**：本地估算 × 安全系数（建议 **×3**，覆盖中转膨胀）> token_max×0.85 时强制压缩/截断——**必须做**。
  2. **工具输出源头截断**：read_file 等大输出默认 ≤6–8K 字符，从根上把本地 req 从 143 万字符压到 ~15 万，即使 ×3 膨胀也不超限——**根治**。
  3. 修正 deepseek 系本地计数（作为辅助，让 UI 显示更接近真实）。

> 注意：模型报错 "in the messages" 指 messages 参数本身。若中转把 system/tools 也并入 messages 统计，膨胀倍数会更高——安全系数 ×3 是下限，建议实际观察多轮后校准。

&nbsp;
