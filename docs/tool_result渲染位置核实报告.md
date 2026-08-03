# tool_result 渲染位置核实报告（修正版）

> 问题：tool_result 应该进「工具调用」面板，却显示在「用户对话」区（UI 上带 `> Agent305` 名称前缀）。
> 范围：核实前端事件路由与渲染逻辑，并追溯后端事件分类泄漏通道。
> 日期：2026-08-04（修正 2026-08-04 早前版本）

---

## 一、结论先行

**前端路由机制本身是正确的** —— 只要是 `tool_result` 类型帧，前端 100% 路由到「工具调用」面板（workflow 块），**不会**渲染成对话气泡。

**但存在一条真实的实时泄漏通道**：后端流式标签解析器**未拦截 `tool_result` 标签**（`_output_handler.py` 的 `_handlers` 有 `tool_call` 而无 `tool_result`），一旦 LLM 输出中混入工具结果文本（模型回显 `<tool_result>` 块、或裸工具 dict），就会经 `_default_handler → emit_user_stream → to_user_stream → stream 帧` 实时推到前端，前端按协议把 `stream`/`message` 帧渲染为**对话区流式文本 / assistant 气泡**（带 `> Agent305` 名称）。

**判定**：不是纯前端展示 bug，而是**后端事件分类泄漏 + 前端忠实渲染**的配合问题。早前「不可能进入 to_user_stream」的结论**不完整**，本版补充泄漏通道证据。

---

## 二、两条路径：为什么「tool_result 事件」与「你看到的工具 JSON」不是一回事

### 路径 A —— 工具结果 → `tool_result` 事件（正确，进工具面板）

```
_turn_loop.py L647  _tool_result_text = str(result)   ← 单引号 Python repr（就是你看到的格式）
  → L722 add_event("tool_result", {id, name, args, result})
  → L728 _emit("tool_result", {id, name, args, result})
  → gateway_adapter.on_tool_result (L982) → _send_event(content, "tool_result")
  → 前端 AIChatPage L2591 → appendWorkflowEvent → workflow 块 → SoloActivityRow（工具面板）
```

这条链路**只渲染在工具面板**，不带 `> Agent305` 前缀，工具行标题是工具名（如 `read_file` / `Read launcher_main.py L300-L500`）。

### 路径 B —— 工具结果文本混入 LLM 输出（泄漏，进对话区）

```
LLM 流式输出出现工具结果文本（<tool_result>...</tool_result> 或裸 `{'status': ...}`）
  → xml_parser.py：tag_name="tool_result" 不在 _handlers（_output_handler.py L91-109）
  → L203-205 _emit_default(self._head_buf) → 当作普通文本
  → _default_handler = emit_user_stream（L89）→ to_user_stream 事件
  → gateway_adapter.on_runner_stream（L888）→ _send_event("stream")
  → 前端 AIChatPage L2244 onWs('stream') → streamingTextRef → StreamingMessage（L8572，带 senderName）
  → 轮次结束 to_user_final → on_runner_output（L849）→ _send_event("message")
  → 前端 handleFinal（L2269）→ assistant 气泡（MessageBubble，带 `> Agent305`）
```

**`> Agent305` 是消息气泡 / 流式消息专属标识**（`AIChatPage.tsx` L8351-8352 / L8578 `senderName={agentProfile?.agent_name}`）。工具面板（`SoloActivityRow` / `ToolCallBlock`）从不渲染这个名字。因此你看到的 `> Agent305` + 工具 JSON，**必然来自路径 B**。

---

## 三、前端事件路由核实明细

### 实时路径

| 事件类型 | 路由（代码位置） | 渲染位置 |
|---|---|---|
| `tool_call` / `tool_result` / `tool_call_delta` | `AIChatPage.tsx` L2528/2558/2591；`useExecSessionLiveTimeline.ts` L351-406 → `appendWorkflowEvent` | ✅ 工具调用面板 |
| `thought` / `plan` / `info` | `appendWorkflowEvent` | ✅ 工具调用面板 |
| `stream` | `AIChatPage.tsx` L2244 → `StreamingMessage` | ⚠️ 对话区（流式预览） |
| `message` / `response` / `to_user_final` / `to_user_reply` / `to_user_end_task` | `AIChatPage.tsx` L2269 `handleFinal` → `MessageBubble` | ⚠️ 对话区（assistant 气泡） |

**前端对 `stream` / `message` 帧不做内容甄别** —— 它信任后端已完成事件分类，因此后端一旦把工具结果放进 `to_user_stream` / `to_user_final`，前端就按设计显示在对话区。

### 刷新 / 历史路径（正确）

- `utils/aiChatTimeline.ts` L1477：`if (m.role === 'tool') continue;` 显式跳过。
- events 中的 `tool_result` 经 `convertSessionEventsToWorkflow`（L2144）合并进 `tool_call` → workflow 块。
- 配套测试 `aiChatTimeline.test.ts` L240-254 验证「跳过 role=tool、不泄漏到消息区」。

### 落盘实测（agent305 / 20260803_151735_a7s7，254 条消息）

| 项 | 值 |
|---|---|
| messages | user 6 / assistant 127 / tool 121 |
| assistant 非空 | 仅 6 条（1 正常报告 + **5 条 400 错误**） |
| assistant 含工具 JSON（`{'status':` / `read_range`） | **0 条** |
| tool 消息（role='tool'） | 121 条（全部工具结果） |

→ **刷新后**工具 JSON 全部在 `role='tool'`（被跳过），对话区干净。

---

## 四、为什么「实时显示异常、刷新后消失」

泄漏内容进入 `_streamed_user_text`（实时累积）后，**落盘时会被清理**：

1. `_runner/_tag_utils.py` `compose_user_visible_message` L189-203 的 `interfering` 列表**包含 `tool_result`** → `remove_tags` 删除 `<tool_result>` 块。
2. `turn_result_handler.py` L172：`if composed and len(composed) > len(user_msg) + 80: user_msg = composed` —— 实时流中的泄漏文本被更长的、已清理的 `composed` 覆盖。
3. 实测会话文件：121/127 条 assistant 为空 → **落盘无污染**。

这解释了：实时看到工具 JSON 在对话区滚动，**刷新后消失**，难以复现和排查。

---

## 五、附带发现（同一会话的可见性问题）

1. **5 条 400 错误被保存为 assistant 消息**（`type='api_sync'`）：
   ```
   [Error: BadRequestError - Error code: 400 - {'error': {'message': "This model's maximum context length is 1048576 tokens..."}}]
   ```
   错误被当作正常回复展示，误导用户以为模型「回答」了报错。

2. **agent305 连续 8 轮重复 `filesystem__read_file`**（`agent.log` L15242 Repeated-Action Guard 警告），tool token 23:30-23:32 从 305K 涨到 364K —— 本次大上下文 + 400 的助燃剂。

---

## 六、修复建议

| 优先级 | 措施 | 位置 | 说明 |
|---|---|---|---|
| P0 | `_handlers` 补注册 `tool_result`（拦截为空） | `_output_handler.py` L91-109 | 堵住标签泄漏通道 |
| P0 | `to_user_stream` / `to_user_final` 内容做工具结果特征检测（`{'status': ...` / `read_range` / `total_lines`），命中则丢弃或转 `tool_result` | `emit_user_stream`（L63）/ `on_runner_output`（L849） | 覆盖裸工具 JSON 无标签泄漏 |
| P1 | 前端 `stream` / `handleFinal` 增加防御过滤：内容若匹配工具结果 dict 特征则不渲染为对话文本 | `AIChatPage.tsx` L2244 / L2269 | 兜底，防同类泄漏上屏 |
| P1 | 400/API 错误不落盘为 assistant 消息、不当作正常回复展示 | `chat_api.py` / `_turn_loop.py` | 解决「错误当回答」误导 |
| P2 | 重复工具调用（Repeated-Action Guard）强制断循环或等待用户 | `_turn_loop.py` | 防止 143 万字符上下文堆积 |

---

## 七、证据索引

| 证据 | 位置 |
|---|---|
| 后端 `tool_result` 事件发送 | `_runner/_turn_loop.py` L722-731 |
| `_handlers` 漏拦截 `tool_result`（有 tool_call） | `_runner/_output_handler.py` L91-109 |
| XML 未注册标签走 default | `xml_parser.py` L203-205 |
| default → to_user_stream | `_output_handler.py` L63-69、L89 |
| to_user_stream → stream 帧 | `gateway_adapter.py` L888、L953-966 |
| to_user_final → message 帧 | `gateway_adapter.py` L849-865、`turn_result_handler.py` L219 |
| 前端 stream 帧渲染 | `components/AIChatPage.tsx` L2244、L8572 |
| 前端 message 帧渲染 | `components/AIChatPage.tsx` L2269、L8351 |
| tool_result 帧路由到 workflow | `components/AIChatPage.tsx` L2591 |
| 刷新跳过 role='tool' | `utils/aiChatTimeline.ts` L1477 |
| 落盘清理 `<tool_result>` | `_runner/_tag_utils.py` L189-203 |
| 会话文件实测 | `agents/agent305/data/history/20260803_151735_a7s7.json` |
| 重复 read_file 循环 | `agents/agent305/data/logs/agent.log` L15242 |

---

## 八、一句话结论

**tool_result 事件本身没有渲染错位置（路径 A 正确）；但存在后端标签解析漏拦截 `tool_result`（路径 B），使工具结果文本作为 `stream`/`message` 帧实时泄漏到对话区（`> Agent305` 前缀证实），刷新后被落盘清理掩盖。修复优先级：补拦截 `tool_result` 标签 + 对 to_user 内容做工具结果特征检测。**
