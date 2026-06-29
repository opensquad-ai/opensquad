# -*- coding: utf-8 -*-
import time

import requests
import json
from urllib.parse import urlencode

# API server address
# BASE_URL = "http://123.207.199.223:9000"
BASE_URL = "http://127.0.0.1:9000"
def run_get_api_test():
    """
    Execute a complete API test flow: search first, then fetch content based on results, all using GET methods.
    """
    print("="*20 + " Starting GET Method Web Search Service Test " + "="*20)

    # # --- Step 1: Call /search endpoint ---
    # print("\n--- [Step 1] Calling /search endpoint... ---")
    # # 2025 humanoid robot trends, 2025 humanoid robot development, humanoid robot breakthroughs
    # # Convert list to comma-separated string - humanoid robot development trends, industry report, tech breakthroughs
    # queries_list = ['2025 humanoid robot industry trends']
    # search_params = {
    #     "queries": ",".join(queries_list),
    #     "max_results": 30
    # }
    #
    # # Use urlencode to build the query string; requests will handle URL encoding automatically
    # search_url = f"{BASE_URL}/search"
    #
    # try:
    #     print(f"Sending GET request to: {search_url}")
    #     print(f"Query params: {search_params}")
    #
    #     response = requests.get(search_url, params=search_params, timeout=120)
    #     response.raise_for_status()
    #
    #     search_response_data = response.json()
    #
    #     print("\n--- [Step 1] /search endpoint responded successfully! ---")
    #     print(json.dumps(search_response_data, indent=2, ensure_ascii=False))
    #
    #     search_results = search_response_data.get("data", [])
    #
    # except requests.exceptions.RequestException as e:
    #     print(f"\n--- [Error] Failed to call /search endpoint: {e} ---")
    #     return
    #
    # if not search_results:
    #     print("\n--- No search results found, test complete. ---")
    #     return

    # --- Step 2: Call /fetch endpoint ---
    print("\n\n--- [Step 2] Calling /fetch endpoint... ---")
    urls_list=[
        "https://www.chinairn.com/news/20250625/171247928.shtml",
    "https://finance.sina.com.cn/roll/2025-01-03/doc-inecsewy8977601.shtml",
    "https://www.sohu.com/a/903288203_121155505",
    "https://www.chinairn.com/scfx/20250227/090955105.shtml",
    "https://baike.baidu.com/item/2025%E5%B9%B4%E4%BA%BA%E5%BD%A2%E6%9C%BA%E5%99%A8%E4%BA%BA%E4%BA%A7%E4%B8%9A%E5%8F%91%E5%B1%95%E8%93%9D%E7%9A%AE%E4%B9%A6/65574570"
    ]
    # Extract URL list from previous step's results
    # urls_list = [result['url'] for result in search_results if 'url' in result]
    # urls_list=['https://www.zhihu.com/question/11318483997',
    #            'https://www.zhihu.com/question/662659251',
    #            'https://www.zhihu.com/question/10775107971',
    #            'https://www.zhihu.com/question/14108993324','https://www.zhihu.com/question/8426110666']
    if not urls_list:
        print("--- No URLs available to fetch, test complete. ---")
        return
    # time.sleep(1000)
    fetch_params = {"urls": ",".join(urls_list)}
    fetch_url = f"{BASE_URL}/fetch"

    try:
        print(f"Sending GET request to: {fetch_url}")
        print(f"Query params: {fetch_params}")

        response = requests.get(fetch_url, params=fetch_params, timeout=180)
        response.raise_for_status()
        
        fetch_response_data = response.json()
        
        print("\n--- [Step 2] /fetch endpoint responded successfully! ---")
        print(json.dumps(fetch_response_data, indent=2, ensure_ascii=False))

    except requests.exceptions.RequestException as e:
        print(f"\n--- [Error] Failed to call /fetch endpoint: {e} ---")

    print("\n" + "="*25 + " API Test Complete " + "="*25)
