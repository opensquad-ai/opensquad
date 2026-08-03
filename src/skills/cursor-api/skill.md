---
name: cursor-api
description: 通过 Cursor REST API 调用智能体的完整指南，支持持续对话和流式输出（Server-Sent Events）
version: 1.0.0
author: Agent305
---

# Cursor API 调用智能体指南

> Cursor 提供 REST API (`https://api.cursor.sh`)，可以通过 HTTP 协议调用其内置 AI 智能体，
> 支持**持续对话**（会话上下文保持）和**流式输出**（Server-Sent Events）。

## 前置条件

- 一个 **Cursor 账号**（注册获取 API Key）
- **API Key**：在 Cursor Settings > General > API 中生成
- 知道目标 **Agent ID**（不同模型对应不同 ID，见下文）

## API 端点

| 端点 | 用途 |
|------|------|
| `POST /v1/chat/completions` | Chat 会话（支持多轮对话，推荐） |
| `POST /v1/completions` | 单次代码补全（无上下文保持） |

## 模型 / Agent ID

| ID | 模型 | 说明 |
|----|------|------|
| `cursor-sonnet-4k` | Claude Sonnet | 默认 Agent 模型 |
| `cursor-gpt-4` | GPT-4 | 备用 |
| `cursor-claude-3.5-sonnet` | Claude 3.5 Sonnet | 最新 Sonnet |
| `claude-3.5-sonnet-20240620` | Claude 3.5 Sonnet (精确) | 精确模型名 |

## 核心用法

### 1. 基础 Chat 调用（非流式）

```python
import httpx

API_KEY = "sk-xxx"
BASE = "https://api.cursor.sh/v1"

resp = httpx.post(
    f"{BASE}/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": "cursor-sonnet-4k",  # 模型 ID
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Write a Python function to merge two dicts."},
        ],
        "max_tokens": 4096,
        "temperature": 0.7,
        "stream": False,  # 非流式
    },
    timeout=60,
)

data = resp.json()
print(data["choices"][0]["message"]["content"])
```

### 2. 流式输出（SSE，推荐）

```python
import httpx
import json

API_KEY = "sk-xxx"
BASE = "https://api.cursor.sh/v1"

with httpx.Client() as client:
    with client.stream(
        "POST",
        f"{BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "cursor-sonnet-4k",
            "messages": [
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "Explain Python decorators."},
            ],
            "max_tokens": 4096,
            "stream": True,  # 启用流式
        },
        timeout=120,
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                if delta := chunk["choices"][0].get("delta", {}):
                    if content := delta.get("content", ""):
                        print(content, end="", flush=True)
```

### 3. 持续对话（多轮上下文保持）

持续对话的关键：**将历史消息全部传回 `messages` 数组**。

```python
messages = [
    {"role": "system", "content": "You are a coding assistant."},
    {"role": "user", "content": "Write a binary search in Python."},
]

# ——— 第1轮 ———
resp = client.post(f"{BASE}/chat/completions", json={"model": "cursor-sonnet-4k", "messages": messages})
reply1 = resp.json()["choices"][0]["message"]["content"]
messages.append({"role": "assistant", "content": reply1})

# ——— 第2轮（用户追问） ———
messages.append({"role": "user", "content": "Now make it handle duplicates (first occurrence)."})
resp = client.post(f"{BASE}/chat/completions", json={"model": "cursor-sonnet-4k", "messages": messages})
reply2 = resp.json()["choices"][0]["message"]["content"]
messages.append({"role": "assistant", "content": reply2})

# 此时 messages 包含了完整对话历史，下一轮继续追加即可
```

> **要点**：每一轮都把 messages 完整传过去，服务端无状态，上下文完全由客户端维护。

### 4. Code 补全（Editor API）

```python
resp = httpx.post(
    f"{BASE}/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "model": "cursor-sonnet-4k",
        "prompt": "def quick_sort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[",
        "suffix": "\n    return sorted_left + [pivot] + sorted_right",
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": True,
    },
)
```

### 5. curl 示例（快速测试）

```bash
# 非流式
curl https://api.cursor.sh/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-sonnet-4k",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# 流式
curl https://api.cursor.sh/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model": "cursor-sonnet-4k", "messages": [{"role":"user","content":"Hello!"}], "stream": true}'
```

## 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `model` | string | 模型 ID，见上表 |
| `messages` | array | 消息列表，每个元素 `{role, content}` |
| `max_tokens` | int | 最大生成 token 数，默认 4096 |
| `temperature` | float | 采样温度 0-2，默认 0.7 |
| `top_p` | float | nucleus sampling，默认 1 |
| `stream` | bool | 是否流式输出 SSE，默认 false |
| `stop` | string[] | 停止词，可选 |

## 重要特性

### 持续对话
- Cursor API 是**无状态**的：服务端不保存会话历史
- 上下文完全由客户端维护：每次请求把完整 `messages` 数组传过去
- 包含 system prompt 可以在每轮对话中保持角色一致性

### 流式输出
- stream=True 时，响应通过 **Server-Sent Events (SSE)** 推送
- 每行格式：`data: {...json chunk...}`
- 结束标记：`data: [DONE]`
- 逐 token 推送，适合打字机效果

### 限流
- 速率限制取决于账号套餐
- 建议在客户端实现**指数退避重试**

## 完整示例：Chat Agent 封装

```python
class CursorAgent:
    """封装 Cursor Chat API 的简易 Agent。"""

    def __init__(self, api_key: str, model: str = "cursor-sonnet-4k"):
        self.api_key = api_key
        self.model = model
        self.base = "https://api.cursor.sh/v1"
        self.messages = [{"role": "system", "content": "You are a helpful coding assistant."}]

    def ask(self, prompt: str, stream: bool = False) -> str:
        """发送消息，返回回复内容。"""
        self.messages.append({"role": "user", "content": prompt})

        resp = httpx.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": self.messages,
                "stream": stream,
                "max_tokens": 4096,
            },
            timeout=120,
        )

        if stream:
            full_text = ""
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    if delta := chunk["choices"][0].get("delta", {}):
                        full_text += delta.get("content", "")
            reply = full_text
        else:
            reply = resp.json()["choices"][0]["message"]["content"]

        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def reset(self):
        self.messages = [{"role": "system", "content": "You are a helpful coding assistant."}]


# 使用
agent = CursorAgent(api_key="sk-xxx")
print(agent.ask("Write a binary search."))
print(agent.ask("Now make it recursive."))  # 上下文保持
```

## 注意事项

1. **API Key 安全**：不要硬编码在代码中，使用环境变量 `CURSOR_API_KEY`
2. **上下文长度**：Cursor 模型支持长上下文（>100K token），但过长的 messages 会增加延迟，建议定期裁剪
3. **限流处理**：429 响应时用指数退避重试
4. **流式 vs 非流式**：实时对话用 stream=True，非交互场景用 stream=False
5. **模型可用性**：不同套餐可用的模型不同，建议先查看 Cursor 最新文档确认
