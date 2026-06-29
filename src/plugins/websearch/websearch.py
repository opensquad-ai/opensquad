# -*- coding: utf-8 -*-
import requests
import json
import logging
import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from opensquad.system_config import syscfg
from typing import List, Dict, Optional

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
        mid = enc.decode(tokens[mid_start:mid_start + part_tokens])
        last = enc.decode(tokens[-part_tokens:])
    else:
        chars = part_tokens * 4
        first = text[:chars]
        mid_start = max(0, len(text) // 2 - chars // 2)
        mid = text[mid_start:mid_start + chars]
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
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = json.load(_f)
            if "port" in _cfg:
                return f"http://127.0.0.1:{int(_cfg['port'])}"
        except Exception:
            pass
    return syscfg.websearch_url()


def _make_request(endpoint: str, params: Dict) -> Dict:
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


def search(queries: List[str], max_results: int = 30) -> List[Dict[str, str]]:
    """
    Call the WebSearch service's /search endpoint to retrieve search results for multiple queries.
    Usage tips:
    - **Concept expansion and synonym substitution**: When keywords fail to return useful results,
      try synonyms. E.g., expand "artificial intelligence" to "machine learning", "deep learning",
      "neural networks", "LLM", etc.
    - **Multi-angle queries:** For complex questions, don't rely on a single keyword. Use multiple
      related, different-angle queries (the `queries` list) to get more comprehensive information.
    - **Cross-validation of results:** Overlapping results from different queries generally indicate
      more reliable sources.
    - **Summary-driven content retrieval:** Don't visit all returned links directly. First read the
      `snippet` carefully (summaries include date/time info), then select only the most relevant and
      authoritative links, and use the `fetch` tool to retrieve the full text.
    - **High result volume:** When fetch results are insufficient, increase max_results, e.g., 30 => 100.
    demo: How to research "the latest advances in artificial intelligence"**
    1.  **Define multi-angle queries:** `search(queries=["Latest AI breakthroughs 2024", "Top AI conference papers 2024", "Gartner 2024 AI report"], max_results=30)`.
    2.  **Analyze summaries:** Review the returned results' `title` and `snippet`, identifying specific
        technologies (e.g., "multimodal large models", "AI Agent") or authoritative sources (e.g., MIT, Google AI).
    3.  **Precise content retrieval:** Pass the 2-3 most relevant `url` values to the `fetch` tool for in-depth reading.
    """
    logger.info(f"Executing 'search' tool for queries: {queries}")
    max_results = int(max_results)
    params = {
        "queries": ",".join(queries),
        "max_results": max_results,
    }
    return _make_request("search", params)


def fetch(urls: List[str], max_token=100000) -> Dict[str, str]:
    """
    Call the WebSearch service's /fetch endpoint to retrieve the body content of one or more URLs.
    **Best Practices:**
    - **Batch processing:** If multiple URLs need to be fetched simultaneously, pass them in a single list
                 rather than making multiple calls. E.g., fetch(urls=["url_to_article_A", "url_to_article_B"]).
    - **Use after search:** This tool is typically used after the `search` tool to deeply investigate
                 high-value links identified from search results.
    - **Token limit:** Total output is capped at max_token (default 100k tokens). If exceeded, content
                 is split into 3 parts (beginning, middle, end) to preserve context diversity.
    """
    max_token = int(max_token)
    logger.info(f"Executing 'fetch' tool for {len(urls)} URLs.")
    params = {
        "urls": ",".join(urls)
    }
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


def fetch_html(url: str = None) -> Dict:
    """
       Call the WebSearch service's /fetch_html endpoint to retrieve the raw HTML content of a URL.

       **Best Practices:**
       - **For deep search:** This tool is generally used in deep search tasks. A webpage often contains
       a large amount of text and link addresses. Deep searches often require identifying new search
       directions from the page's HTML links, making this a key step in determining critical links
       for the search task.
       - **Use after search:** This tool is typically used after the `search` tool to deeply investigate
       high-value filtered links.

       """
    logger.info(f"Executing 'fetch_html' tool for {url} URL.")
    params = {
        "url": url
    }
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
