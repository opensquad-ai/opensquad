import trafilatura
from bs4 import BeautifulSoup


def is_html(content: str) -> bool:
    """
    Quickly determine whether content is HTML by checking for <html> or <body> tags.
    This is a simple but efficient heuristic.
    """
    if not content:
        return False
    # Check whether the string contains common HTML tags
    return content.strip().startswith("<") and ("<html" in content.lower() or "<body" in content.lower())


def wash_content(content: str, url: str) -> str:
    """
    Intelligently clean the input content.
    If the content is HTML, perform deep cleaning using trafilatura and BeautifulSoup.
    If the content is plain text (e.g. extracted from a PDF), return it directly.

    :param content: Web page HTML or plain text content.
    :param url: The URL the content came from, used for logging.
    :return: Cleaned plain text.
    """
    if not content:
        return ""

    # --- Step 1: Determine content type ---
    if not is_html(content):
        print(f"--- Content from {url} is plain text (likely from PDF), skipping HTML wash. ---")
        return content

    # --- Step 2: If HTML, execute the cleaning pipeline ---
    print(f"--- Content from {url} is HTML, proceeding with wash. ---")

    # Priority content selectors
    content_selectors = [
        "div.article-content",
        "article",
        "main",
        'div[class*="post-content"]',
        'div[class*="entry-content"]',
    ]

    soup = BeautifulSoup(content, "html.parser")

    # Try to focus content using priority selectors
    focused_content = None
    for selector in content_selectors:
        element = soup.select_one(selector)
        if element:
            print(f"--- Content washing: Successfully focused on container: '{selector}' ---")
            focused_content = str(element)
            break

    if not focused_content:
        print("--- Content washing: Warning - No specific container found, falling back to <body> ---")
        focused_content = content

    # Use trafilatura to extract the main content
    extracted_text = trafilatura.extract(
        focused_content, include_comments=False, include_tables=True, no_fallback=True
    )  # Use no_fallback=True to avoid extracting the entire body

    if extracted_text:
        print("--- Content washing: Successfully extracted text with trafilatura. ---")
        return extracted_text
    else:
        # If trafilatura fails, fall back to BeautifulSoup
        print("--- Content washing: Trafilatura failed, falling back to BeautifulSoup's get_text(). ---")
        if focused_content:
            # Re-parse the focused content
            soup_focused = BeautifulSoup(focused_content, "html.parser")
            return soup_focused.get_text(separator="\n", strip=True)
        else:
            # If no focused content either, use the original soup
            return soup.get_text(separator="\n", strip=True)
