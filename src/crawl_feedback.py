import csv
import os
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ----------------------------
# Configuration
# ----------------------------

PUBLIC_SAFETY_SITEMAP = "https://www.canada.ca/en/public-safety-canada.sitemap.xml"
PUBLIC_SAFETY_EXCLUDE_PREFIXES = [
    "https://www.canada.ca/en/public-safety-canada/news/"
]

SERVICES_SITEMAP = "https://www.canada.ca/en/services.sitemap.xml"
SERVICES_PREFIXES = [
    "https://www.canada.ca/en/services/defence/nationalsecurity",
    "https://www.canada.ca/en/services/defence/securingborder",
    "https://www.canada.ca/en/services/policing",
]

FEEDBACK_MARKER = (
    'data-ajax-replace="/etc/designs/canada/wet-boew/assets/feedback/page-feedback-en.html"'
)

OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "page_feedback_report.csv")

HEADERS = {
    "User-Agent": "canada-page-feedback-crawler/1.0",
    "Accept-Language": "en-CA,en;q=0.9",
}

TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0


# ----------------------------
# Helpers
# ----------------------------

def strip_namespace(tag: str) -> str:
    """Remove XML namespace from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def is_english_url(url: str) -> bool:
    """
    Keep English pages only.
    For canada.ca, this typically means /en/... or /en.html
    """
    path = urlparse(url).path.lower()
    return path == "/en.html" or path.startswith("/en/")


def is_excluded_public_safety_url(url: str) -> bool:
    return any(url.startswith(prefix) for prefix in PUBLIC_SAFETY_EXCLUDE_PREFIXES)


def is_services_target_url(url: str) -> bool:
    """Keep only the requested services subsections."""
    return any(url.startswith(prefix) for prefix in SERVICES_PREFIXES)


def normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def fetch_text(session: requests.Session, url: str) -> str:
    response = session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def parse_sitemap(xml_text: str):
    """
    Returns:
      ("urlset", [page URLs]) or
      ("sitemapindex", [child sitemap URLs])
    """
    root = ET.fromstring(xml_text)
    root_name = strip_namespace(root.tag)

    locs = []
    for elem in root.iter():
        if strip_namespace(elem.tag) == "loc" and elem.text:
            locs.append(elem.text.strip())

    if root_name == "urlset":
        return "urlset", locs
    elif root_name == "sitemapindex":
        return "sitemapindex", locs
    else:
        raise ValueError(f"Unsupported sitemap root element: {root_name}")


def collect_urls_from_sitemap(session: requests.Session, sitemap_url: str):
    """
    Supports both:
      - regular sitemap files (<urlset>)
      - sitemap index files (<sitemapindex>)
    """
    sitemap_queue = [sitemap_url]
    seen_sitemaps = set()
    page_urls = []

    while sitemap_queue:
        current_sitemap = sitemap_queue.pop(0)

        if current_sitemap in seen_sitemaps:
            continue

        seen_sitemaps.add(current_sitemap)

        xml_text = fetch_text(session, current_sitemap)
        sitemap_type, locs = parse_sitemap(xml_text)

        if sitemap_type == "sitemapindex":
            sitemap_queue.extend(locs)
        elif sitemap_type == "urlset":
            page_urls.extend(locs)

        time.sleep(REQUEST_DELAY_SECONDS)

    return page_urls


def unique_urls(urls):
    seen = set()
    ordered = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def extract_title(html: str) -> str:
    """
    Extract page title from HTML.
    Beautiful Soup supports using soup.title or tag search methods
    for HTML parsing.
    """
    soup = BeautifulSoup(html, "html.parser")

    if soup.title and soup.title.get_text():
        return normalize_text(soup.title.get_text())

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_text(og_title["content"])

    return ""


def inspect_page(session: requests.Session, url: str):
    try:
        html = fetch_text(session, url)
        title = extract_title(html) or "[No title found]"
        has_feedback = "Yes" if FEEDBACK_MARKER in html else "No"
        return {
            "Title": title,
            "URL": url,
            "Page Feedback Yes/No": has_feedback,
        }
    except Exception as exc:
        return {
            "Title": f"[Error: {type(exc).__name__}]",
            "URL": url,
            "Page Feedback Yes/No": "No",
        }


def inspect_page_threadsafe(url: str):
    session = requests.Session()
    return inspect_page(session, url)


def process_urls_parallel(urls, max_workers=8):
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(inspect_page_threadsafe, url) for url in urls]

        for i, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            print(f"[{i}/{len(urls)}] Completed: {result['URL']}")
            results.append(result)

    return results

def build_target_url_list(session: requests.Session):
    # 1) Public Safety sitemap -> English only
    public_safety_urls = collect_urls_from_sitemap(session, PUBLIC_SAFETY_SITEMAP)
    public_safety_urls = [u for u in public_safety_urls if is_english_url(u) and not is_excluded_public_safety_url(u)]

    # 2) Services sitemap -> English only + requested prefixes only
    services_urls = collect_urls_from_sitemap(session, SERVICES_SITEMAP)
    services_urls = [
        u for u in services_urls
        if is_english_url(u) and is_services_target_url(u)
    ]

    return unique_urls(public_safety_urls + services_urls)


def write_csv(rows):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Title", "URL", "Page Feedback Yes/No"]
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    session = requests.Session()

    target_urls = build_target_url_list(session)
    print(f"Found {len(target_urls)} target URLs from sitemap sources.")

    rows = process_urls_parallel(target_urls, max_workers=8)

    write_csv(rows)
    print(f"Done. Report written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
