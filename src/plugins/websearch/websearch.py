import json
import logging
import os as _os
import sys

import requests

sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))

from opensquad.system_config import syscfg

logger = logging.getLogger("plugins.websearch")

TIMEOUT = 60  # Request timeout in seconds (search=60s, fetch=60s)

# Token counting encoder (lazy init)
_encoding = None


def _get_encoding():
    global _encoding
    if _encoding is None:
        try:
            import tiktoken

            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoding = False
    return _encoding if _encoding is not False else None


def _count_tokens(text: str) -> int:
    enc = _get_encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4


def _truncate_content_by_tokens(text: str, max_tokens: int) -> str:
    if _count_tokens(text) <= max_tokens:
        return text
    part_tokens = max_tokens // 3
    if part_tokens <= 0:
        return ""
    enc = _get_encoding()
    if enc:
        tokens = enc.encode(text)
        first = enc.decode(tokens[:part_tokens])
        mid_start = max(0, len(tokens) // 2 - part_tokens // 2)
        mid = enc.decode(tokens[mid_start : mid_start + part_tokens])
        last = enc.decode(tokens[-part_tokens:])
    else:
        chars = part_tokens * 4
        first = text[:chars]
        mid_start = max(0, len(text) // 2 - chars // 2)
        mid = text[mid_start : mid_start + chars]
        last = text[-chars:]
    return f"{first}\n\n[…中间内容已截断…]\n\n{mid}\n\n[…中间内容已截断…]\n\n{last}"


def _get_service_url() -> str:
    """
    Dynamically resolve the WebSearch service URL.
    Port priority (aligned with Launcher / plugin service):
    1. PORT env (Launcher child process)
    2. workspace data/plugins/websearch/config.json
    3. services.websearch_url or ports.websearch via syscfg.websearch_url()
    """
    port_env = _os.environ.get("PORT")
    if port_env:
        try:
            return f"http://127.0.0.1:{int(port_env)}"
        except ValueError:
            pass
    _cfg_path = syscfg.workspace_data_dir("plugins", "websearch", "config.json")
    if _os.path.isfile(_cfg_path):
        try:
            with open(_cfg_path, encoding="utf-8") as _f:
                _cfg = json.load(_f)
            if "port" in _cfg:
                return f"http://127.0.0.1:{int(_cfg['port'])}"
        except Exception:
            pass
    return syscfg.websearch_url()


def _make_request(endpoint: str, params: dict) -> dict:
    """
    A generic function for sending GET requests to the WebSearch API.
    """
    url = f"{_get_service_url()}/{endpoint}"
    logger.info(f"Sending GET request to: {url}")
    logger.debug(f"Request params: {params}")

    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()  # Raise an exception if status code is not 2xx

        response_data = response.json()
        logger.info(f"API response received successfully from {endpoint}.")
        logger.debug(f"Response data: {json.dumps(response_data, indent=2, ensure_ascii=False)}")

        # Return the content of the 'data' field, or a dict with an error message if absent
        return response_data.get("data", {"error": "API response did not contain a 'data' field."})

    except requests.exceptions.RequestException as e:
        logger.error(f"API call to {url} failed: {e}", exc_info=True)
        return {"error": f"Failed to call API endpoint '{endpoint}': {e}"}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON response from {url}: {e}", exc_info=True)
        return {"error": f"Invalid JSON response from API endpoint '{endpoint}'."}


def search(
    queries: list[str] | str | None = None,
    max_results: int = 30,
    query: str | list[str] | None = None,
) -> list[dict[str, str]]:
    """
    Call the WebSearch service's /search endpoint to retrieve search results for multiple queries.

    Each result typically includes: title, url, summary/snippet, relevance_score,
    matched_keywords, matched_queries, and optionally result_type/card_kind
    (``answer_card`` for Bing weather/knowledge/AI widgets; ``organic`` for blue links).

    Usage tips:
    - **Prefer snippets / answer cards before fetch:** For many questions (weather, facts,
      short news, definitions), the returned ``summary``/``snippet`` or an ``answer_card``
      already contains enough information to answer. Do **not** automatically call
      ``fetch``/``fetch_html`` on every URL. Only fetch when you still need deeper detail
      that the snippet clearly lacks.
    - **Answer cards first:** If any result has ``result_type="answer_card"`` (especially
      ``card_kind="weather"`` / entity / ai_answer), read that summary first — it mirrors
      what a human sees at the top of the Bing page.
    - **Concept expansion and synonym substitution**: When keywords fail to return useful
      results, try synonyms. E.g., expand "artificial intelligence" to "machine learning",
      "deep learning", "neural networks", "LLM", etc.
    - **Multi-angle queries:** For complex questions, don't rely on a single keyword. Use
      multiple related, different-angle queries (the ``queries`` list).
    - **Language-aware search:** Chinese queries automatically use cn.bing.com; English
      queries use www.bing.com. Mixed queries pick the region based on the dominant script.
    - **Multi-keyword queries:** You can pass comma-separated related keywords in one query
      string, e.g. ``queries=["福州天气, 福州气温, 福州降雨"]``. Results include
      ``matched_keywords`` showing which keyword phrases actually hit the page.
    - **Cross-validation:** Higher ``match_count``, ``relevance_score``, or more
      ``matched_keywords`` usually means better intent match.
    - **When to fetch:** After reading summaries, fetch at most 1–3 high-value URLs that
      still need full text (long reports, docs, paywalled-looking snippets that are thin).
      Prefer alternative open sources if a site is known to block scrapers.
    - **High result volume:** If coverage is thin, increase max_results (e.g. 30 → 100)
      and re-rank by summary — still avoid mass-fetching.

    Demo: research "the latest advances in artificial intelligence"
    1. Multi-angle search: ``search(queries=["Latest AI breakthroughs 2024", ...], max_results=30)``
    2. Read ``title`` / ``summary`` / ``relevance_score`` / ``result_type``; answer from
       snippets/answer cards when sufficient.
    3. Only then ``fetch`` the 1–3 URLs that still need full-page detail.
    """
    if not queries:
        if query is None:
            return {"error": "Missing required parameter 'queries'."}
        queries = [query] if isinstance(query, str) else list(query)
    elif isinstance(queries, str):
        queries = [queries]

    logger.info(f"Executing 'search' tool for queries: {queries}")
    max_results = int(max_results)
    params = {
        "queries": ",".join(queries),
        "max_results": max_results,
    }
    return _make_request("search", params)


def fetch(urls: list[str], max_token=100000) -> dict[str, str]:
    """
    Call the WebSearch service's /fetch endpoint to retrieve the body content of one or more URLs.

    **When to use:** Only after ``search``, and only for links whose ``summary``/``snippet``
    (or answer card) is insufficient. Do not fetch every search hit.

    **Best Practices:**
    - **Snippet-first:** If search already answers the user (weather card, short fact, clear
      snippet), skip fetch entirely.
    - **Batch processing:** Pass multiple URLs in one list instead of repeated calls.
    - **Anti-bot / Forbid pages:** Many sites (e.g. weather.com.cn) return block pages such as
      ``Forbid_code: 120000``. That is a site WAF rejection, not a tool bug. Do **not** retry
      the same URL with ``fetch`` or ``fetch_html``. Instead: rely on search snippets/answer
      cards, pick another source URL, or use an interactive browser/MCP scrape channel if
      available.
    - **Token limit:** Total output is capped at max_token (default 100k tokens). If exceeded,
      content is split into beginning/middle/end segments.
    """
    max_token = int(max_token)
    logger.info(f"Executing 'fetch' tool for {len(urls)} URLs.")
    params = {"urls": ",".join(urls)}
    res_dict = _make_request("fetch", params)
    if not isinstance(res_dict, dict):
        return res_dict
    result = {}
    total_tokens = 0
    token_budget = max_token
    for url, content in res_dict.items():
        if not isinstance(content, str):
            result[url] = content
            continue
        content_tokens = _count_tokens(content)
        if total_tokens + content_tokens <= token_budget:
            result[url] = content
            total_tokens += content_tokens
        else:
            remaining = token_budget - total_tokens
            if remaining > 0:
                result[url] = _truncate_content_by_tokens(content, remaining)
                total_tokens = token_budget
            break
    if not result:
        combined = "\n\n---\n\n".join(
            f"【{url}】\n{content}" for url, content in res_dict.items() if isinstance(content, str)
        )
        result["_combined"] = _truncate_content_by_tokens(combined, max_token)
    return result


def fetch_html(url: str | None = None) -> dict:
    """
    Call the WebSearch service's /fetch_html endpoint to retrieve the raw HTML of a URL.

    **When to use:** Deep-link discovery or structure inspection — not as the default way to
    read page text. Prefer ``search`` summaries/answer cards, then ``fetch`` for cleaned text.

    **Best Practices:**
    - Use after search when you need page links/structure beyond the snippet.
    - Same anti-bot rule as ``fetch``: ``Forbid_code`` / soft-block HTML means switch source
      or use snippets; do not loop fetch_html on the blocked URL.
    """
    logger.info(f"Executing 'fetch_html' tool for {url} URL.")
    params = {"url": url}
    return _make_request("fetch_html", params)


# --- Optional standalone test section ---
# if __name__ == '__main__':
#     # Before running this test, ensure the WebSearch API service is running at http://127.0.0.1:9000
#
#     print("--- Testing WebSearch Tool: search() ---")

#     print(json.dumps(search_results, indent=2, ensure_ascii=False))
#
#     if isinstance(search_results, list) and search_results and "error" not in search_results[0]:
#         urls_to_fetch = [res['url'] for res in search_results if 'url' in res]
#         if urls_to_fetch:
#             print("\n--- Testing WebSearch Tool: fetch() ---")
#             fetch_results = fetch(urls=urls_to_fetch)
#             print(json.dumps(fetch_results, indent=2, ensure_ascii=False))
#         else:
#             print("\n--- No URLs found to fetch. ---")
#     else:
#         print("\n--- Search failed or returned no results, skipping fetch test. ---")
