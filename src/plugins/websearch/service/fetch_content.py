import asyncio

import httpx

# SSL verification: use certifi CA bundle on Windows
try:
    import certifi

    _SSL_VERIFY = certifi.where()
except ImportError:
    _SSL_VERIFY = True
import contextlib

from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import Error as PlaywrightError

# from playwright_stealth import stealth_async
# from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Reusable Stealth instance (playwright_stealth.Stealth can be expensive to
# instantiate and may race when multiple pages call apply_stealth_async
# concurrently with separate instances).
_stealth_instance: Stealth | None = None

try:
    from .pdf_processor import extract_text_from_pdf
except ImportError:
    from pdf_processor import extract_text_from_pdf


async def fetch_page_content_async(context: BrowserContext, url: str, retries: int = 1) -> str:
    """
    Asynchronously fetch the content of a single page.
    If the URL points to a PDF file, it will be downloaded directly and text extracted.
    Otherwise, a shared browser context is used to fetch HTML content.
    This is a robust version with automatic retry support.

    :param context: An already-launched and configured Playwright browser context.
    :param url: The URL of the web page or PDF.
    :param retries: (Optional) Number of retries on failure.
    :return: Page HTML content or PDF text, or None if all attempts fail.
    """
    # --- Step 1: Check if the URL is a PDF ---
    if url.lower().endswith(".pdf"):
        print(f"---  Detected PDF, using direct download for: {url} ---")
        async with httpx.AsyncClient(verify=_SSL_VERIFY) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, follow_redirects=True, timeout=30)
                    response.raise_for_status()  # Raise an exception if the status code is not 2xx

                    pdf_content = response.content
                    text = extract_text_from_pdf(pdf_content)

                    if text:
                        print(f"--- [OK] Successfully extracted text from PDF: {url} ---")
                        return text
                    else:
                        print(f"--- [FAIL] Failed to extract text from PDF (empty content): {url} ---")
                        return None  # Even if the download succeeded, treat as failure if PDF content is empty or unparseable

                except httpx.HTTPStatusError as e:
                    print(f"--- [FAIL] HTTP Error on attempt {attempt + 1}/{retries + 1} for PDF {url}: {e} ---")
                except httpx.RequestError as e:
                    print(f"--- [FAIL] Request Error on attempt {attempt + 1}/{retries + 1} for PDF {url}: {e} ---")

                if attempt < retries:
                    await asyncio.sleep(2)
                else:
                    print(f"--- [FAIL] All attempts failed to download PDF: {url}. ---")
                    return None
        return None

    # --- Step 2: If not a PDF, use Playwright to fetch HTML ---
    content_selectors = [
        "div.article-content",
        "article",
        "main",
        'div[class*="post-content"]',
        'div[class*="entry-content"]',
    ]

    # Reuse a single Stealth instance (creating one per page is wasteful
    # and can race on concurrent calls).
    global _stealth_instance
    if _stealth_instance is None:
        _stealth_instance = Stealth()

    for attempt in range(retries + 1):
        page = None
        try:
            page = await context.new_page()
            # Apply stealth before navigation. Wrapped in try/except because
            # stealth script injection can fail on about:blank (pre-navigation)
            # or race with concurrent page creation, raising non-PlaywrightError
            # exceptions (RuntimeError, AttributeError) that would bubble up
            # as 500 errors.
            try:
                await _stealth_instance.apply_stealth_async(page)
            except Exception as stealth_err:
                print(f"--- [WARN] Stealth injection skipped (non-fatal): {stealth_err} ---")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            with contextlib.suppress(PlaywrightError):
                await page.wait_for_selector(",".join(content_selectors), state="attached", timeout=5000)

            content = await page.content()
            await page.close()

            print(f"--- [OK] Successfully fetched HTML from: {url} ---")
            return content

        except PlaywrightError as e:
            print(f"--- [FAIL] Playwright Error on attempt {attempt + 1}/{retries + 1} for {url}: {e} ---")
            if page and not page.is_closed():
                await page.close()
            if attempt < retries:
                await asyncio.sleep(2)
            else:
                print(f"--- [FAIL] All Playwright attempts failed for {url}. ---")
                return None
        except Exception as e:
            # Catch non-PlaywrightError exceptions (RuntimeError, etc.) that
            # would otherwise bubble up as 500 Internal Server Error.
            print(f"--- [FAIL] Unexpected error on attempt {attempt + 1}/{retries + 1} for {url}: {e} ---")
            if page and not page.is_closed():
                await page.close()
            if attempt < retries:
                await asyncio.sleep(2)
            else:
                print(f"--- [FAIL] All attempts failed for {url} (unexpected error). ---")
                return None
    return None


async def main():
    # --- Demo: how to run this module standalone ---
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(ignore_https_errors=True)

        # --- Test 1: Fetch an HTML page ---
        html_url_to_test = "https://www.whatismybrowser.com/"
        print(f"--- Testing HTML fetch on URL: {html_url_to_test} ---")
        html_content = await fetch_page_content_async(context, html_url_to_test)
        if html_content:
            print(f"\n--- Successfully fetched HTML (first 200 chars): ---\n{html_content[:200]}...")
        else:
            print("\n--- Failed to fetch HTML. ---")

        # --- Test 2: Fetch a PDF ---
        # Note: Replace the URL below with a real publicly accessible PDF link
        pdf_url_to_test = "https://arxiv.org/pdf/1706.03762.pdf"  # A well-known PDF
        print(f"\n--- Testing PDF fetch on URL: {pdf_url_to_test} ---")
        pdf_text = await fetch_page_content_async(context, pdf_url_to_test)
        if pdf_text:
            print(f"\n--- Successfully fetched PDF text (first 500 chars): ---\n{pdf_text[:500]}...")
        else:
            print("\n--- Failed to fetch PDF. ---")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
