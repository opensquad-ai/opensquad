# system.wait() 可中断功能使用指南

## 概述

`system.wait()` 工具现在支持两种等待模式：
- **不可中断模式**（默认）：固定等待，无法被外部事件打断
- **可中断模式**：可被群聊消息等外部事件唤醒

## 函数签名

```python
async def wait(seconds: float, interruptible: bool = False) -> Dict[str, Any]
```

## 参数说明

- `seconds`: 等待的秒数（必须为正数）
- `interruptible`: 是否可被外部事件唤醒（默认 False）
  - `False`: 使用固定等待，无法中断（适合 API 限流、重试间隔等）
  - `True`: 使用可唤醒睡眠，可被群聊消息等外部事件唤醒（适合等待用户回复）

## 返回值

### 不可中断模式 (interruptible=False)

```json
{
  "status": "success",
  "message": "Wait completed for 5.0s."
}
```

### 可中断模式 (interruptible=True)

```json
{
  "status": "success",
  "wake_type": "natural" | "interrupted",
  "planned_seconds": 60,
  "actual_seconds": 45.2,
  "wake_reason": "收到群聊消息: @我 现在有空了",
  "wake_time": "2026-03-01 14:30:15"
}
```

## 使用示例

### 示例 1: API 限流等待（不可中断）

```xml
<tool name="system">
  <function>wait</function>
  <parameters>
    <seconds>5</seconds>
    <interruptible>false</interruptible>
  </parameters>
</tool>
```

适用场景：
- API 调用限流
- 重试延迟
- 固定时间间隔的操作

### 示例 2: 等待用户回复（可中断）

```xml
我已经发送了问题到群聊，现在等待用户回复。

<tool name="system">
  <function>wait</function>
  <parameters>
    <seconds>300</seconds>
    <interruptible>true</interruptible>
  </parameters>
</tool>
```

当用户在群聊中 @你 或发送重要消息时，等待会被中断，你可以立即响应。

### 示例 3: 处理唤醒结果

```xml
<tool name="system">
  <function>wait</function>
  <parameters>
    <seconds>600</seconds>
    <interruptible>true</interruptible>
  </parameters>
</tool>

<!-- 假设返回结果存储在变量 result 中 -->
<!-- Agent 可以检查 wake_type 来判断是否被打断 -->

如果 wake_type == "interrupted":
  说明有新消息到达，原因是: {result["wake_reason"]}
  实际只等待了 {result["actual_seconds"]} 秒

如果 wake_type == "natural":
  说明完整等待了 {result["planned_seconds"]} 秒，没有收到新消息
```

## 对比：wait() vs <sleep> 标签

| 特性 | system.wait(N, interruptible=False) | system.wait(N, interruptible=True) | <sleep>N</sleep> |
|------|-------------------------------------|-----------------------------------|-----------------|
| 可被消息唤醒 | ❌ 否 | ✅ 是 | ✅ 是 |
| 返回唤醒信息 | ❌ 否 | ✅ 是 | ✅ 是 |
| 适用场景 | 固定延迟、限流 | 等待回复、可中断操作 | 等待回复、可中断操作 |
| 调用方式 | 工具调用 | 工具调用 | XML 标签 |

## 建议

1. **默认使用不可中断模式**：如果不需要响应外部事件，使用默认的 `interruptible=False`
2. **需要响应时使用可中断模式**：等待用户回复、等待外部事件时，设置 `interruptible=True`
3. **检查唤醒类型**：可中断模式下，检查 `wake_type` 来判断是自然醒还是被打断
4. **与 <sleep> 标签等价**：`wait(N, interruptible=True)` 在功能上等同于 `<sleep>N</sleep>`，但返回更详细的信息

## 技术实现

- **不可中断模式**：使用 `asyncio.sleep()`
- **可中断模式**：使用全局 `sleep_controller`，支持通过 `sleep_controller.wake_up(reason)` 外部唤醒
- **消息唤醒**：当群聊中有 @提及或重要消息时，`message_router` 会自动调用 `sleep_controller.wake_up()`

## 注意事项

1. **可中断模式需要 sleep_controller**：如果 `sleep_controller` 未正确导入，可中断模式会返回错误
2. **秒数必须为正数**：负数会导致错误
3. **可中断模式使用整数秒**：`interruptible=True` 时，秒数会被转换为 `int`
4. **并发唤醒安全**：多个等待任务不会互相干扰，每个任务独立管理

## 测试

运行测试脚本验证功能：

```bash
python test_interruptible_wait.py
```

测试覆盖：
- ✓ 不可中断等待（默认行为）
- ✓ 可中断等待 - 自然醒
- ✓ 可中断等待 - 被打断

---

**版本**: system.py v2.2
**更新日期**: 2026-03-01
