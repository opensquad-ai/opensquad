# Model Cards Configuration Guide

A Model Card defines how OpenSquad connects to an LLM provider. Each card is a JSON file under `src/model_cards/`.

---

## JSON Fields

### Top-Level Fields

| Field | Type | Required | Description |
|------|------|------|------|
| `name` | string | Yes | Card name (used for `_card` reference) |
| `title` | string | No | Display name |
| `provider` | string | Yes | Interface protocol type |
| `model_name` | string | Yes | Model identifier sent to the API |
| `base_url` | string | Yes | API base URL |
| `api_key` | string | Yes | API key (can also use env var) |
| `token_max` | int | No | Max context tokens (default 128000) |
| `temperature` | float | No | Sampling temperature (default 0.7) |
| `frequency_penalty` | float | No | Frequency penalty |
| `presence_penalty` | float | No | Presence penalty |
| `top_k` | int | No | Top-K sampling parameter |

### Provider Types

| Provider | Protocol | Typical base_url |
|------|------|------|
| `openai` | OpenAI native | `https://api.openai.com/v1` |
| `openai_compat` | OpenAI-compatible | `https://api.deepseek.com` |
| `anthropic` | Anthropic Messages API | `https://api.anthropic.com` |
| `google` | Google Generative AI | `generativelanguage.googleapis.com` |

---

## Full Example

```json
{
  "name": "deepseek-v4-pro",
  "title": "DeepSeek V4 Pro",
  "provider": "openai_compat",
  "model_name": "deepseek-v4-pro",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-your-api-key",
  "token_max": 128000,
  "temperature": 0.7,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "top_k": 0
}
```

---

## Using Model Cards

### In Agent config.json

Reference a card via the `_card` field:

```json
{
  "model": {
    "_card": "deepseek-v4-pro"
  }
}
```

When `_card` is specified, the card's values are used as defaults. Individual fields in `model` can override them:

```json
{
  "model": {
    "_card": "deepseek-v4-pro",
    "temperature": 0.3  // override the card's default
  }
}
```

### In Web UI

The **Model Cards** management page allows you to:
- Create, edit, and delete model cards
- Browse all available providers
- Import/export cards
- Assign cards to Agents

---

## Tool Call Modes

`tool_call_mode` controls how the Agent invokes tools:

| Mode | Description |
|------|------|
| `auto` | Auto-select: uses Native FC if supported, otherwise XML |
| `native` | Force native function calling (OpenAI/Claude/Google format) |
| `xml` | Use XML-format tool calls (compatible with all models, but higher token cost) |

> **Recommendation**: Use `auto` in most cases. Switch to `xml` if the model doesn't support Native FC or has poor FC accuracy.

---

## Model Capability Query (SDK)

```python
from opensquad.model_capabilities import (
    supports_function_calling,
    get_model_capability,
    ModelCapabilityRegistry,
)

# Check if a model supports Function Calling
if supports_function_calling("gpt-4"):
    print("GPT-4 supports FC")

# Get full capabilities
cap = get_model_capability("deepseek-v3")
print(cap.max_context_tokens)  # 128000
print(cap.supports_images)     # False

# List all models supporting FC
models = ModelCapabilityRegistry.get_supported_models()
```
