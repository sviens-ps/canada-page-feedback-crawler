import csv
import os
import time
import json
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup


# ----------------------------
# Load config
# ----------------------------

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------
# Helpers
# ----------------------------

def is_english_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path == "/en.html" or path.startswith("/en/")


def fetch_text(session, url, timeout):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


# ----------------------------
# Sitemap processing
# ----------------------------

def parse_sitemap(xml_text: str):
    root = ET.fromstring(xml_text)
    root_name = strip_namespace(root.tag)

    locs = []
    for elem in root.iter():
        if strip_namespace(elem.tag) == "loc" and elem.text:
            locs.append(elem.text.strip())

    return root_name, locs


def collect_urls_from_sitemap(session, sitemap_url, timeout, delay):
    queue = [sitemap_url]
    seen = set()
    urls = []

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        xml_text = fetch_text(session, current, timeout)
        root_name, locs = parse_sitemap(xml_text)

        if root_name == "sitemapindex":
            queue.extend(locs)
        elif root_name == "urlset":
            urls.extend(locs)

        time.sleep(delay)

    return urls


def build_target_url_list(session, config):
    all_urls = []

    for sitemap in config["sitemaps"]:
        raw_urls = collect_urls_from_sitemap(
            session,
            sitemap["url"],
            config["settings"]["timeout_seconds"],
            config["settings"]["request_delay"]
        )

        filtered = []

        for url in raw_urls:
            if not is_english_url(url):
                continue

            if sitemap["include_prefixes"]:
                if not any(url.startswith(p) for p in sitemap["include_prefixes"]):
                    continue

            if any(url.startswith(p) for p in sitemap["exclude_prefixes"]):
                continue

            filtered.append(url)

        all_urls.extend(filtered)

    return list(dict.fromkeys(all_urls))


# ----------------------------
# Extraction logic
# ----------------------------

def extract_date_modified(soup):
    dt_elements = soup.find_all("dt")

    for dt in dt_elements:
        if "Date modified:" in dt.get_text():
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(strip=True)

    return "Not found"


def run_checks(html, checks):
    results = {}
    soup = BeautifulSoup(html, "html.parser")

    for check in checks:
        name = check["name"]

        if check["type"] == "contains":
            results[name] = "Yes" if check["value"] in html else "No"

        elif check["type"] == "extract":
            if check["method"] == "date_modified":
                results[name] = extract_date_modified(soup)
            else:
                results[name] = "Unsupported method"

        else:
            results[name] = "Unsupported type"

    return results


# ----------------------------
# Page inspection
# ----------------------------

def inspect_page(url, checks, timeout):
    session = requests.Session()

    try:
        html = fetch_text(session, url, timeout)

        soup = BeautifulSoup(html, "html.parser")

        result = {
            "Title": soup.title.get_text(strip=True) if soup.title else "[No title found]",
            "URL": url
        }

        result.update(run_checks(html, checks))
        return result

    except Exception as exc:
        return {
            "Title": f"[Error: {type(exc).__name__}]",
            "URL": url,
            **{check["name"]: "Error" for check in checks}
        }


def process_urls_parallel(urls, config):
    results = []
    max_workers = config["settings"]["max_workers"]
    timeout = config["settings"]["timeout_seconds"]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(inspect_page, url, config["checks"], timeout)
            for url in urls
        ]

        for i, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            print(f"[{i}/{len(urls)}] {result['URL']}")
            results.append(result)

    return results


# ----------------------------
# Output
# ----------------------------

def write_csv(rows, config):
    output_file = config["output"]["file"]
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    fieldnames = ["Title", "URL"] + [c["name"] for c in config["checks"]]

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------
# Main
# ----------------------------

def main():
    config = load_config()
    session = requests.Session()

    target_urls = build_target_url_list(session, config)
    print(f"Found {len(target_urls)} target URLs.")

    rows = process_urls_parallel(target_urls, config)

    write_csv(rows, config)

    print("Done.")


if __name__ == "__main__":
    main()
