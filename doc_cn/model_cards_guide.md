# 模型卡配置指南

模型卡（Model Card）是 OpenSquad 中用于描述 LLM 模型连接信息的配置文件。每个模型卡定义了模型名称、API 地址、密钥、能力参数等，Agent 通过引用模型卡来使用特定的 LLM。

---

## 模型卡文件结构

模型卡是 `src/model_cards/` 目录下的 JSON 文件，文件名即为模型卡的 `name`（例如 `deepseek-v4-pro.json`）。

### 完整字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 模型卡唯一标识符，与文件名一致 |
| `title` | string | 否 | 显示名称，用于 UI 展示 |
| `api_protocol` | string | 是 | 接口协议类型，见下方说明 |
| `provider` | string | 否 | 模型供应商（厂商）名称，用于 UI 展示/分组 |
| `api_key` | string | 是 | API 密钥 |
| `base_url` | string | 是 | API 基础地址 |
| `model_name` | string | 是 | 模型名称（传给 API 的 model 参数） |
| `token_max` | number | 否 | 最大上下文 token 数，默认 128000 |
| `temperature` | number | 否 | 采样温度，范围 0-2，默认 0.7 |
| `frequency_penalty` | number | 否 | 频率惩罚（OpenAI 参数），默认 0 |
| `presence_penalty` | number | 否 | 存在惩罚（OpenAI 参数），默认 0 |
| `top_k` | number | 否 | Top-K 采样（非 OpenAI 模型常用），默认 0 |
| `is_think` | boolean | 否 | 是否启用思考模式（推理链），默认 false |
| `is_image` | boolean | 否 | 是否支持图像输入，默认 false |
| `is_audio` | boolean | 否 | 是否支持音频输入，默认 false |
| `is_video` | boolean | 否 | 是否支持视频输入，默认 false |
| `is_audio_output` | boolean | 否 | 是否支持音频输出，默认 false |
| `is_image_output` | boolean | 否 | 是否支持图像输出，默认 false。对 `openai`/`openai_compat`：会走 `/v1/images/generations` 文生图；对 `google`：解析聊天响应中的图片 |
| `image_size` | string | 否 | 文生图尺寸（OpenAI Images API），默认 `1024x1024` |
| `image_steps` | number | 否 | 文生图采样步数（StepFun 等），默认 8 |
| `image_cfg_scale` | number | 否 | 文生图 CFG（StepFun 等），默认 1.0 |
| `audio_output_voice` | string | 否 | 音频输出语音角色，默认 "alloy" |

### Agent `voice` 段（StepAudio）

在 Agent `config.json` 中可配置：

```json
"voice": {
  "asr_card": "stepaudio-2.5-asr",
  "tts_card": "stepaudio-2.5-tts",
  "realtime_card": "stepaudio-2.5-realtime",
  "realtime_voice": "linjiajiejie"
}
```

- `asr_card`：`asr_tts.transcribe_audio_file` 使用的 ASR 模型卡
- `tts_card`：`asr_tts.synthesize_speech` 使用的 TTS 模型卡（仅工具主动调用时合成）
- `realtime_card`：Agent Web 实时通话使用的 Realtime 模型卡
| `tool_call_mode` | string | 否 | 工具调用模式：`auto`/`native`/`xml`，默认 `auto` |
| `render_mode` | string | 否 | 渲染模式：`strict`（严格）/`full`（完整），默认 `strict` |
| `enable_repetition_check` | boolean | 否 | 是否启用重复检测，默认 false |

> **字段命名说明（自 v0.x 版本起）**
> - `api_protocol`：决定 OpenSquad 与 LLM API 通信时使用哪种协议（OpenAI / Anthropic / Google 等）。这是技术层面的接口选择。
> - `provider`：模型供应商 / 厂商名称（如 `DeepSeek`、`OpenAI`、`Google Gemini`），仅用于 UI 展示和分组，**不影响实际调用协议**。
> 之前的版本中这两个字段被分别命名为 `provider`（接口协议）和 `vendor_name`（厂商），新的命名更符合语义。

### api_protocol 类型

`api_protocol` 字段决定了 OpenSquad 如何与 LLM API 通信：

| api_protocol 值 | 说明 | 适用场景 |
|-------------|------|----------|
| `openai` | OpenAI 官方 API 格式 | ChatGPT、GPT-4 等 |
| `openai_compat` | OpenAI 兼容 API | DeepSeek、GLM、Qwen、Moonshot 等绝大多数国产模型 |
| `anthropic` | Anthropic Claude API | Claude 系列模型 |
| `google` | Google Gemini API | Gemini 系列模型 |

> **注意**：如果你的模型提供 OpenAI 兼容接口（绝大多数国产模型都支持），选择 `openai_compat` 即可。

---

## 示例

### DeepSeek V4 Pro（openai_compat）

```json
{
  "name": "deepseek-v4-pro",
  "title": "DeepSeek V4 Pro",
  "api_protocol": "openai_compat",
  "provider": "DeepSeek",
  "api_key": "sk-your-deepseek-api-key",
  "base_url": "https://api.deepseek.com",
  "model_name": "deepseek-v4-pro",
  "token_max": 128000,
  "temperature": 0.7,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "top_k": 0,
  "is_think": true,
  "is_image": false,
  "is_audio": false,
  "is_video": false,
  "is_audio_output": false,
  "is_image_output": false,
  "audio_output_voice": "alloy",
  "render_mode": "strict"
}
```

### Gemini 3.1 Pro Preview（google）

```json
{
  "name": "gemini-3.1-pro-preview",
  "title": "Gemini 3.1 Pro Preview",
  "api_protocol": "google",
  "provider": "Google Gemini",
  "api_key": "AIzaSy...",
  "base_url": "https://generativelanguage.googleapis.com/v1beta",
  "model_name": "gemini-3.1-pro-preview",
  "token_max": 1048576,
  "temperature": 0,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "top_k": 0,
  "is_think": true,
  "is_image": true,
  "is_audio": false,
  "is_video": true,
  "is_audio_output": false,
  "is_image_output": false,
  "audio_output_voice": "alloy",
  "render_mode": "strict"
}
```

---

## 模型能力注册

OpenSquad 内置了一个模型能力注册表（`model_capabilities.py`），为已知模型自动配置能力参数。当你的模型卡中不设置某些能力字段时，系统会根据 `model_name` 自动匹配：

- `supports_function_calling`：是否支持原生函数调用（Native FC）
- `supports_streaming`：是否支持流式输出
- `supports_images`：是否支持图像输入
- `max_context_tokens`：最大上下文长度

已注册的模型包括：GPT-4 系列、Claude 系列、Gemini 系列、DeepSeek 系列、GLM 系列、Qwen 系列、Kimi 系列等。

对于未注册的模型，系统会返回保守的默认配置（假设不支持 Native FC，使用 XML 模式）。

---

## 使用模型卡

### 1. 创建模型卡

在 `src/model_cards/` 目录下创建 JSON 文件，例如 `my-model.json`：

```json
{
  "name": "my-model",
  "title": "My Custom Model",
  "api_protocol": "openai_compat",
  "provider": "MyVendor",
  "api_key": "sk-xxx",
  "base_url": "https://api.example.com/v1",
  "model_name": "my-model-v1",
  "token_max": 128000,
  "temperature": 0.7
}
```

### 2. 通过 Web UI 管理

启动 OpenSquad 后，在 Web UI 的 **模型卡管理** 页面可以：
- 查看所有模型卡
- 创建、编辑、删除模型卡
- 按 Provider 筛选
- 收藏常用模型卡
- 将模型卡分配给 Agent

### 3. 分配给 Agent

在 Agent 管理页面，选择目标 Agent，然后选择对应的模型卡即可。Agent 配置中的 `_card` 字段会记录引用的模型卡名称。

### 4. 模型预设

在模型卡编辑页面，可以使用 **模型预设（Preset）** 功能快速填充厂商和模型信息。预设数据来自 models.dev 和 OpenRouter，可以一键填充 base_url、model_name、温度等参数。

---

## 工具调用模式

`tool_call_mode` 控制 Agent 如何调用工具：

| 模式 | 说明 |
|------|------|
| `auto` | 自动选择：如果模型支持 Native FC 则使用 native，否则使用 XML |
| `native` | 强制使用原生函数调用（OpenAI/Claude/Google 格式） |
| `xml` | 使用 XML 格式的工具调用（兼容所有模型，但 token 消耗较大） |

> **建议**：大多数情况下使用 `auto`。如果模型不支持 Native FC 或 FC 准确率低，切换到 `xml` 模式。

---

## 模型能力查询（SDK）

```python
from opensquad.model_capabilities import (
    supports_function_calling,
    get_model_capability,
    ModelCapabilityRegistry,
)

# 检查模型是否支持 Function Calling
if supports_function_calling("gpt-4"):
    print("GPT-4 supports FC")

# 获取完整能力
cap = get_model_capability("deepseek-v3")
print(cap.max_context_tokens)  # 128000
print(cap.supports_images)     # False

# 列出支持 FC 的模型
models = ModelCapabilityRegistry.get_supported_models()
```
