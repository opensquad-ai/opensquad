"""
Model Preset Service

Data sources:
  1. models.dev /api.json — fetched on manual refresh; provides model list and capability info for all vendors
  2. OpenRouter /v1/models — fetched on manual refresh; used only to build the openrouter vendor model list

On startup: loads data from last refresh saved in disk file preset_cache.json, no network access.
Manual refresh (POST /refresh): re-fetches the above two data sources and overwrites the disk file.

Public interface:
  await initialize()     — called on application startup (lifespan), read-only disk
  get_presets()          — called by route handlers, returns in-memory cache
  await manual_refresh() — manually triggers a full refresh and overwrites the disk file
  shutdown()             — called on application shutdown (no-op, interface kept)
"""

import json
import logging
import os
import time

import httpx

# SSL verification: use certifi CA bundle on Windows where system store may be unavailable
try:
    import certifi

    _SSL_VERIFY = certifi.where()
except ImportError:
    _SSL_VERIFY = True

logger = logging.getLogger(__name__)

from .model_presets_static import STATIC_PRESETS


# ── Vendor metadata (base_url is provided here; neither data source includes this info) ────────────────────────
# Order determines the sort order of the frontend dropdown menu
# icon_url: uses Google Favicon service (free and stable), sz=64 retrieves 64px icons
def _gf(domain: str) -> str:
    """Build Google Favicon service URL"""
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


VENDOR_META: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("deepseek.com"),
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_protocol": "openai",
        "icon_url": _gf("openai.com"),
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "base_url": "https://api.anthropic.com",
        "api_protocol": "anthropic",
        "icon_url": _gf("anthropic.com"),
    },
    "google": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_protocol": "google",
        "icon_url": _gf("google.com"),
    },
    "qwen": {
        "label": "Alibaba Qwen (Tongyi Qianwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("aliyun.com"),
    },
    "zhipuai": {
        "label": "Zhipu GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_protocol": "openai_compat",
        "icon_url": _gf("bigmodel.cn"),
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("moonshot.cn"),
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("minimax.chat"),
    },
    "mistral": {
        "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("mistral.ai"),
    },
    "cohere": {
        "label": "Cohere",
        "base_url": "https://api.cohere.com/v2",
        "api_protocol": "openai_compat",
        "icon_url": _gf("cohere.com"),
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("groq.com"),
    },
    "perplexity": {
        "label": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "api_protocol": "openai_compat",
        "icon_url": _gf("perplexity.ai"),
    },
    "together_ai": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("together.ai"),
    },
    "baidu": {
        "label": "Baidu ERNIE",
        "base_url": "https://qianfan.baidubce.com/v2",
        "api_protocol": "openai_compat",
        "icon_url": _gf("baidu.com"),
    },
    "stepfun": {
        "label": "Stepfun Step",
        "base_url": "https://api.stepfun.com/step_plan/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("stepfun.com"),
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("siliconflow.cn"),
    },
    "openrouter": {
        "label": "OpenRouter (aggregator / relay)",
        "base_url": "https://openrouter.ai/api/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("openrouter.ai"),
    },
    "ollama": {
        "label": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_protocol": "openai_compat",
        "icon_url": _gf("ollama.com"),
    },
}

# models.dev provider ID → our vendor_id mapping
# Prioritize China-region endpoints (-cn suffix) for direct access by domestic users
_MODELS_DEV_PROVIDER_MAP: dict[str, str] = {
    "deepseek": "deepseek",
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "alibaba-cn": "qwen",  # domestic endpoint: dashscope.aliyuncs.com
    "zhipuai": "zhipuai",
    "moonshotai-cn": "moonshot",  # domestic endpoint: api.moonshot.cn
    "minimax-cn": "minimax",  # domestic endpoint: api.minimaxi.com
    "mistral": "mistral",
    "cohere": "cohere",
    "groq": "groq",
    "perplexity": "perplexity",
    "togetherai": "together_ai",
    "stepfun": "stepfun",
    "siliconflow-cn": "siliconflow",  # domestic endpoint: api.siliconflow.cn
    # baidu not on models.dev, kept as static fallback
}

MODELS_DEV_URL = "https://models.dev/api.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

from opensquad.system_config import syscfg


# Writable cache under workspace data/ (never next to this module in frozen bundles).
def _cache_file_path() -> str:
    path = os.path.join(syscfg.workspace_data_dir(), "model_preset_cache.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


# ── Module-level state ────────────────────────────────────────────────────────────────
_cached_presets: dict = {"providers": []}  # Loaded from disk on startup, updated after refresh
_models_dev_data: dict = {}
_openrouter_data: list = []
_last_models_dev_ts: float = 0.0
_last_openrouter_ts: float = 0.0


# ── Helper functions ──────────────────────────────────────────────────────────

_THINK_KEYWORDS = frozenset(
    [
        "reasoner",
        "thinking",
        "think",
        "r1",
        "r2",
        "o1",
        "o3",
        "o4",
        "qwq",
        "z1",
        "deepthink",
        "turbo-s",
    ]
)


def _detect_is_think(model_name: str) -> bool:
    name_lower = model_name.lower()
    return any(kw in name_lower for kw in _THINK_KEYWORDS)


def _build_title(model_name: str) -> str:
    """Convert model_name to a human-readable title, stripping vendor prefix"""
    name = model_name.split("/")[-1]
    return name.replace("-", " ").replace("_", " ").title()


# ── models.dev → vendor_data ──────────────────────────────────────────────────
# Return structure: {vendor_id: {"meta": {label, base_url, api_protocol}, "models": [...]}}


def _build_from_models_dev(models_dev_data: dict) -> dict[str, dict]:
    """
    Parse models.dev JSON, process all vendors, return
      {vendor_id: {"meta": {...}, "models": [...]}}

    - _MODELS_DEV_PROVIDER_MAP provider_id → use the mapped vendor_id
      (for -cn variant renaming, e.g. alibaba-cn → qwen)
    - VENDOR_META vendor_id → override metadata with its label/base_url/api_protocol
    - All other vendors → vendor_id = provider_id, metadata derived from models.dev fields
    """
    result: dict[str, dict] = {}
    seen_models: dict[str, set] = {}  # vendor_id → set of model_id (deduplicated)

    for provider_id, provider_data in models_dev_data.items():
        if not isinstance(provider_data, dict):
            continue

        models = provider_data.get("models", {})
        if not isinstance(models, dict) or not models:
            continue

        # Determine vendor_id
        vendor_id = _MODELS_DEV_PROVIDER_MAP.get(provider_id, provider_id)

        # Determine metadata: VENDOR_META takes priority, otherwise derive from models.dev data
        if vendor_id in VENDOR_META:
            vm = VENDOR_META[vendor_id]
            meta = {
                "label": vm["label"],
                "base_url": vm["base_url"],
                "api_protocol": vm["api_protocol"],
                "icon_url": vm.get("icon_url", ""),
            }
        else:
            pid_lower = provider_id.lower()
            label = provider_data.get("name") or provider_id.replace("-", " ").title()
            base_url = provider_data.get("api") or ""
            if "anthropic" in pid_lower:
                ptype = "anthropic"
            elif provider_id == "google" or "gemini" in pid_lower:
                ptype = "google"
            elif provider_id == "openai":
                ptype = "openai"
            else:
                ptype = "openai_compat"
            # Extract domain from doc field to generate icon_url
            doc_url = provider_data.get("doc") or base_url
            try:
                from urllib.parse import urlparse

                doc_domain = urlparse(doc_url).hostname or ""
                # Strip www. prefix, get main domain
                if doc_domain.startswith("www."):
                    doc_domain = doc_domain[4:]
                icon_url = _gf(doc_domain) if doc_domain else ""
            except Exception:
                icon_url = ""
            meta = {"label": label, "base_url": base_url, "api_protocol": ptype, "icon_url": icon_url}

        # Initialize this vendor (same vendor_id may come from multiple provider_ids, merge and deduplicate)
        if vendor_id not in result:
            result[vendor_id] = {"meta": meta, "models": []}
            seen_models[vendor_id] = set()

        # Build model list
        for model_id, entry in models.items():
            if not isinstance(entry, dict):
                continue
            if model_id in seen_models[vendor_id]:
                continue
            seen_models[vendor_id].add(model_id)

            limit = entry.get("limit") or {}
            token_max = int(limit.get("context") or 4096)
            is_think = bool(entry.get("reasoning", False)) or _detect_is_think(model_id)
            is_image = bool(entry.get("attachment", False))
            modalities_input = (entry.get("modalities") or {}).get("input") or []
            if "image" in modalities_input:
                is_image = True
            is_video = "video" in modalities_input
            tool_call_mode = "native" if entry.get("tool_call", False) else "xml"
            temperature_disabled = not entry.get("temperature", True)
            temp = 0 if (is_think or temperature_disabled) else 0.3
            title = entry.get("name") or _build_title(model_id)

            result[vendor_id]["models"].append(
                {
                    "model_name": model_id,
                    "title": title,
                    "token_max": token_max,
                    "temperature": temp,
                    "is_think": is_think,
                    "is_image": is_image,
                    "is_video": is_video,
                    "tool_call_mode": tool_call_mode,
                }
            )

    return result


# ── OpenRouter merge ───────────────────────────────────────────────────────────


def _merge_openrouter(
    vendor_data: dict[str, dict],
    or_models: list[dict],
) -> dict[str, dict]:
    """
    OpenRouter data is used only to build the openrouter vendor; does not affect other vendors.
    """
    if not or_models:
        return vendor_data

    or_vendor_list: list[dict] = []
    for or_model in or_models:
        or_id = or_model.get("id", "")
        if not or_id:
            continue
        context_length = int(or_model.get("context_length") or 4096)
        modality = or_model.get("architecture", {}).get("modality", "") or ""
        input_part = modality.split("->")[0] if "->" in modality else ""
        is_image = "image" in input_part
        is_video = "video" in input_part
        is_think = _detect_is_think(or_id)
        or_name = or_model.get("name") or _build_title(or_id.split("/")[-1])
        or_vendor_list.append(
            {
                "model_name": or_id,
                "title": or_name,
                "token_max": context_length,
                "temperature": 0 if is_think else 0.3,
                "is_think": is_think,
                "is_image": is_image,
                "is_video": is_video,
                "tool_call_mode": "native",
            }
        )

    if or_vendor_list:
        or_vendor_list.sort(key=lambda m: (m["is_think"], m["model_name"]))
        or_meta = VENDOR_META.get(
            "openrouter",
            {
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_protocol": "openai_compat",
            },
        )
        vendor_data["openrouter"] = {
            "meta": {
                "label": or_meta["label"],
                "base_url": or_meta["base_url"],
                "api_protocol": or_meta["api_protocol"],
            },
            "models": or_vendor_list,
        }
        logger.debug(f"[ModelPresets] openrouter: {len(or_vendor_list)} models from live API")

    return vendor_data


def _assemble_presets(vendor_data: dict[str, dict]) -> dict:
    """
    Assemble into the { providers: [...] } format expected by the frontend.
    Sort order: known vendors in VENDOR_META order (prioritized display), remainder appended alphabetically.

    Response shape (per provider):
      {
        "id":         vendor_id (str),
        "label":      display name (str),
        "provider":   vendor display name (str)  -- 之前叫 vendor_name
        "base_url":   API base URL (str),
        "api_protocol": API protocol (str: openai | openai_compat | anthropic | google)
        "icon_url":   vendor favicon URL (str),
        "models":     [ {model_name, title, token_max, ...}, ... ]
      }
    """
    providers = []
    seen: set = set()

    # 1. Known vendors in VENDOR_META order (displayed first)
    for vendor_id in VENDOR_META:
        vm = VENDOR_META[vendor_id]
        if vendor_id in vendor_data and vendor_data[vendor_id]["models"]:
            d = vendor_data[vendor_id]
            providers.append(
                {
                    "id": vendor_id,
                    "label": d["meta"]["label"],
                    "provider": d["meta"]["label"],
                    "base_url": d["meta"]["base_url"],
                    "api_protocol": d["meta"]["api_protocol"],
                    "icon_url": d["meta"].get("icon_url", vm.get("icon_url", "")),
                    "models": sorted(d["models"], key=lambda m: (m["is_think"], m["model_name"])),
                }
            )
        else:
            # No live model data (e.g. Ollama); still shown as empty entry for user to fill in model name
            providers.append(
                {
                    "id": vendor_id,
                    "label": vm["label"],
                    "provider": vm["label"],
                    "base_url": vm["base_url"],
                    "api_protocol": vm["api_protocol"],
                    "icon_url": vm.get("icon_url", ""),
                    "models": [],
                }
            )
        seen.add(vendor_id)

    # 2. Remaining vendors from models.dev appended alphabetically
    for vendor_id in sorted(vendor_data.keys()):
        if vendor_id not in seen and vendor_data[vendor_id]["models"]:
            d = vendor_data[vendor_id]
            providers.append(
                {
                    "id": vendor_id,
                    "label": d["meta"]["label"],
                    "provider": d["meta"]["label"],
                    "base_url": d["meta"]["base_url"],
                    "api_protocol": d["meta"]["api_protocol"],
                    "icon_url": d["meta"].get("icon_url", ""),
                    "models": sorted(d["models"], key=lambda m: (m["is_think"], m["model_name"])),
                }
            )

    return {"providers": providers}


# ── Persistent cache (disk read/write) ───────────────────────────────────────


def _save_cache_to_disk(presets: dict) -> None:
    """Write current preset data to preset_cache.json for use on next startup"""
    try:
        with open(_cache_file_path(), "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
        n_p = len(presets.get("providers", []))
        n_m = sum(len(p["models"]) for p in presets.get("providers", []))
        cache_path = _cache_file_path()
        logger.info(f"[ModelPresets] Cache saved to disk: {n_p} providers, {n_m} models → {cache_path}")
    except Exception as exc:
        logger.warning(f"[ModelPresets] Failed to save cache to disk: {exc}")


def _load_cache_from_disk() -> dict | None:
    """Read previously persisted preset data from preset_cache.json; return None if missing or parse fails"""
    cache_path = _cache_file_path()
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("providers"):
            n_p = len(data["providers"])
            n_m = sum(len(p["models"]) for p in data["providers"])
            logger.info(f"[ModelPresets] Loaded cache from disk: {n_p} providers, {n_m} models")
            return data
    except Exception as exc:
        logger.warning(f"[ModelPresets] Failed to load cache from disk: {exc}")
    return None


# ── Network requests ──────────────────────────────────────────────────────────


async def _fetch_models_dev() -> dict:
    async with httpx.AsyncClient(timeout=30.0, verify=_SSL_VERIFY) as client:
        resp = await client.get(MODELS_DEV_URL)
        resp.raise_for_status()
        return resp.json()


async def _fetch_openrouter() -> list:
    async with httpx.AsyncClient(timeout=15.0, verify=_SSL_VERIFY) as client:
        resp = await client.get(OPENROUTER_URL)
        resp.raise_for_status()
        return resp.json().get("data", [])


# ── Public interface ──────────────────────────────────────────────────────────


async def initialize() -> None:
    """
    Called at application startup (in lifespan).
    Loads only from disk file preset_cache.json (last refresh save), no network.
    If disk file does not exist, providers is an empty list; wait for user to trigger manual_refresh.
    """
    global _cached_presets

    logger.info("[ModelPresets] Initializing (disk-only)...")

    disk_cache = _load_cache_from_disk()
    if disk_cache:
        # Backfill: cache entries may lack `provider` (vendor name); backfill from id or label.
        # Field was renamed from `vendor_name` to `provider` (硬切换；see model_preset_service history).
        for p in disk_cache.get("providers", []):
            if not p.get("provider"):
                vm = VENDOR_META.get(p.get("id", ""))
                p["provider"] = vm["label"] if vm else p.get("label", "")
        _cached_presets = disk_cache
        n_providers = len(_cached_presets["providers"])
        n_models = sum(len(p["models"]) for p in _cached_presets["providers"])
        logger.info(f"[ModelPresets] Ready from disk: {n_providers} providers, {n_models} models")
    else:
        # No persisted cache yet (e.g. a fresh offline deployment): fall back to
        # the bundled static vendor/model list so providers are still configurable.
        from copy import deepcopy

        _cached_presets = deepcopy(STATIC_PRESETS)
        n_providers = len(_cached_presets["providers"])
        n_models = sum(len(p["models"]) for p in _cached_presets["providers"])
        logger.info(
            f"[ModelPresets] No disk cache; using bundled static fallback: {n_providers} providers, {n_models} models"
        )


def get_presets() -> dict:
    """Return the current cached presets (for synchronous route calls). Never None."""
    return {
        **_cached_presets,
        "meta": {
            "models_dev_fetched_at": _last_models_dev_ts,
            "openrouter_fetched_at": _last_openrouter_ts,
            "source": "live"
            if _last_models_dev_ts
            else ("disk_cache" if os.path.exists(_cache_file_path()) else "static_fallback"),
        },
    }


async def manual_refresh() -> dict:
    """
    Manually trigger a full refresh (models.dev + OpenRouter).
    On success, overwrites disk file preset_cache.json and updates in-memory cache.
    Called by the POST /api/ai-web/model-presets/refresh endpoint.
    """
    global _cached_presets, _models_dev_data, _openrouter_data
    global _last_models_dev_ts, _last_openrouter_ts

    logger.info("[ModelPresets] Manual refresh triggered")
    errors: list = []

    # Fetch models.dev
    try:
        _models_dev_data = await _fetch_models_dev()
        _last_models_dev_ts = time.time()
        logger.info(f"[ModelPresets] models.dev loaded: {len(_models_dev_data)} providers")
    except Exception as exc:
        errors.append(f"models.dev: {exc}")
        logger.warning(f"[ModelPresets] models.dev fetch failed: {exc}")

    # Fetch OpenRouter
    try:
        _openrouter_data = await _fetch_openrouter()
        _last_openrouter_ts = time.time()
        logger.info(f"[ModelPresets] OpenRouter loaded: {len(_openrouter_data)} models")
    except Exception as exc:
        errors.append(f"OpenRouter: {exc}")
        logger.warning(f"[ModelPresets] OpenRouter fetch failed: {exc}")

    # Merge: models.dev provides all vendors; openrouter is used only to build the openrouter vendor
    vendor_models = _build_from_models_dev(_models_dev_data)
    # models.dev is the PRIMARY catalog source. If it failed (offline / blocked),
    # do NOT rebuild the cache from a degraded result (e.g. openrouter alone would
    # leave every known vendor with 0 models and clobber the good cached/static
    # list). Only overwrite when models.dev actually produced vendor data.
    models_dev_ok = bool(vendor_models)
    vendor_models = _merge_openrouter(vendor_models, _openrouter_data)
    assembled = _assemble_presets(vendor_models)

    n_models_new = sum(len(p["models"]) for p in assembled["providers"])

    if models_dev_ok and n_models_new > 0:
        # Successfully fetched model data — update in-memory cache and overwrite disk static file
        _cached_presets = assembled
        _save_cache_to_disk(assembled)
        logger.info("[ModelPresets] Manual refresh complete, disk file updated")
    else:
        # models.dev unavailable (offline / network error) — keep existing cache,
        # do not overwrite disk file with a degraded/partial result.
        logger.warning("[ModelPresets] models.dev unavailable; keeping existing cache intact")

    n_providers = len(_cached_presets["providers"])
    n_models = sum(len(p["models"]) for p in _cached_presets["providers"])
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "providers": n_providers,
        "models": n_models,
        "source": "live" if _last_models_dev_ts else "disk_cache",
    }


def shutdown() -> None:
    """Cleanup hook on application shutdown (no background tasks currently; kept for lifespan interface)"""
    logger.info("[ModelPresets] Shutdown")
