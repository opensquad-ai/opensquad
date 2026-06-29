# -*- coding: utf-8 -*-
"""
Tag utility module — XML/HTML tag extraction and filtering functions.

Extracted from runner.py (AgentRunner static/utility methods) to reduce its size.
All functions are pure: they operate on text and return text, with no dependency
on AgentRunner state.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def filter_native_tokens(text: str) -> str:
    """Filter leaked native tool call text from various models:
    1. <|...|> format (Qwen3/DeepSeek, etc.)
    2. functions.<name>:<id>{...} format (Kimi/Moonshot, etc.)
    """
    if not text:
        return text

    # --- Format 1: <|...|> format (Qwen3/DeepSeek) ---
    if '<|' in text:
        # First remove the entire tool_calls_section block
        text = re.sub(
            r'<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>',
            '', text, flags=re.DOTALL,
        )
        # Fallback: remove all remaining <|...|> tokens
        text = re.sub(r'<\|[^|>]*\|>', '', text)

    # --- Format 2: functions.<name>:<id>{...} format (Kimi/Moonshot) ---
    if 'functions.' in text:
        text = re.sub(
            r'\bfunctions\.[a-zA-Z0-9_]+:\d+\{(?:[^{}]|\{[^{}]*\})*\}',
            '', text, flags=re.DOTALL,
        )

    return text


def remove_all_tags(text: str) -> str:
    """Minimally and thoroughly remove all XML/HTML format tags and their content,
    preserving Markdown formatting.
    """
    if not text:
        return ""

    result = text

    # 0a. Filter native tool call tokens (<|...|> format)
    result = filter_native_tokens(result)

    # 0. Special handling: remove possibly missing-'<' tool_call markers
    result = re.sub(r'tool_call\s+name="[^"]+"\s*>', "", result, flags=re.IGNORECASE)

    # 1. Thoroughly remove these blocks and their content
    silent_blocks = [
        "thought", "plan", "think", "tool_call", "tool_result", "to_system",
        "state", "wake", "sleep", "title", "option", "arguments",
    ]
    for tag in silent_blocks:
        result = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>", "", result,
            flags=re.DOTALL | re.IGNORECASE,
        )
        result = re.sub(rf"<{tag}\b[^>]*/>", "", result, flags=re.IGNORECASE)

    # 2. Special handling for to_user tag: keep its content
    result = re.sub(
        r'<to_user\b[^>]*>(.*?)</to_user>', r'\1', result,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 3. Remove remaining tag names but keep content (if any)
    result = re.sub(r'<[^>]+>', '', result)

    # 4. Thoroughly clean up remaining orphaned closing tags
    result = re.sub(r'</[a-zA-Z0-9_]+>', '', result)
    result = re.sub(r'^\s*[<>]\s*$', '', result, flags=re.MULTILINE)

    # 5. Clean up extra blank lines while preserving necessary breaks
    result = re.sub(r'\n{4,}', '\n\n\n', result)

    return result.strip()


def remove_tags(text: str, tags: list) -> str:
    """Remove specified XML tags (supports tags with attributes).
    Uses a while loop so nested tags are fully stripped."""
    if not text:
        return ""
    result = text
    for tag in tags:
        # Non-greedy match for paired tags — iterate until no more matches
        pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"
        while re.search(pattern, result, flags=re.DOTALL | re.IGNORECASE):
            result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
        # Self-closing tags
        pattern = rf"<{tag}\b[^>]*/>"
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
        # Fallback: if no closing tag (incomplete model output), truncate from open tag to end
        pattern = rf"<{tag}\b[^>]*>.*"
        result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
    return result.strip()


def extract_tag(response: str, tag: str) -> Optional[str]:
    """Robustly extract XML tag content, supporting tag attributes and extra whitespace.

    Tries in order:
    1. Fully matched <tag>...</tag>
    2. Unclosed tag (content up to next <)
    3. Lazy matching for possibly missing '<' (for state/wake/sleep tags)
    """
    # Match <tag ...>content</tag>
    pattern = rf"<{tag}\b[^>]*>(.*?)</{tag}>"
    match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        logger.debug("[Extractor] Found tag <%s>: %s", tag, val)
        return val

    # Fallback 1: if no closing tag, try extracting up to the next < symbol
    pattern_fallback = rf"<{tag}\b[^>]*>(.*)"
    match_fb = re.search(pattern_fallback, response, re.IGNORECASE | re.DOTALL)
    if match_fb:
        val = match_fb.group(1).split('<')[0].strip()
        logger.debug("[Extractor] Found unclosed tag <%s>: %s", tag, val)
        return val

    # Fallback 2: support possibly missing '<' (for lazy AI output patterns)
    if tag in ["state", "wake", "sleep"]:
        pattern_lazy = rf"{tag}\s*>\s*(.*?)\s*</{tag}>"
        match_lazy = re.search(pattern_lazy, response, re.IGNORECASE)
        if match_lazy:
            val = match_lazy.group(1).strip()
            logger.debug("[Extractor] Found lazy tag %s: %s", tag, val)
            return val

    return None


def extract_text_before_tool(text: str) -> Optional[str]:
    """Extract text content before a tool call marker."""
    if not text:
        return None

    tool_match = re.search(r'<tool_call', text, re.IGNORECASE)
    if not tool_match:
        return None

    text_before = text[:tool_match.start()]
    text_before = re.sub(r'<(?!tool_call)[^>]+>', '', text_before)
    text_before = text_before.strip()

    if text_before and len(text_before) > 3:
        return text_before

    return None
