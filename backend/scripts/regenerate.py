#!/usr/bin/env python3
"""
Regenerate the emojipasta of already-published entries with the current prompt, in place.

Each entry keeps its filename, date, section and article_id; only "headline" and "text" are rewritten. The source
article is found by taking the full headline from the Daily Update run logs, collecting candidate BBC URLs (the
section RSS feeds, then BBC search) and accepting only a URL whose hash matches the entry's article_id exactly.

Usage (from backend/, with the usual .env in place):

  python scripts/regenerate.py --since 2026-09-27 --dry-run
  MAX_RUN_COST_USD=1 python scripts/regenerate.py --since 2026-09-27
"""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(__file__))
import backfill  # noqa: E402  (also puts backend/ on sys.path)
from backfill import pipeline  # noqa: E402


def titles_from_runs(since: str) -> dict[str, tuple[str, str]]:
    """safe_title -> (full headline, section) for every article the Daily Update runs fetched since `since`."""
    out = {}
    for run_id, _ in backfill.list_runs(since):
        for section, title in backfill.scan_run(run_id)["fetched"]:
            out.setdefault(backfill.safe_title(title), (title, section))
    return out


# Sections that used to be published; their recent entries can still be regenerated.
RETIRED_FEEDS = [
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
]


def rss_links() -> dict[str, str]:
    """headline -> link for every item currently in the section RSS feeds."""
    links = {}
    for feed in [s["rss"] for s in pipeline.SECTIONS] + RETIRED_FEEDS:
        root = ET.fromstring(requests.get(feed, timeout=30).content)
        for item in root.findall(".//item"):
            guid = item.find("guid")
            raw = (guid.text if guid is not None else item.find("link").text).split("#")[0]
            links[item.find("title").text.strip()] = raw
    return links


def find_source(title: str, article_hash: str, hash_key: str, rss: dict[str, str]) -> str | None:
    candidates = [rss[title]] if title in rss else []
    try:
        candidates += [href for href, _ in backfill.bbc_search(title)]
    except Exception as exc:
        print(f"  search failed for '{title}': {exc}")
    for url in dict.fromkeys(candidates):
        for raw in (url, url.replace("https://www.bbc.co.uk", "https://www.bbc.com")):
            if pipeline.hash_article_id(raw, hash_key) == article_hash:
                return raw
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="YYYY-MM-DD; entries dated on/after this are regenerated")
    parser.add_argument("--dry-run", action="store_true", help="resolve sources only, no Grok calls")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only", nargs="+", help="regenerate only entries whose filename contains one of these")
    parser.add_argument(
        "--skip-modified", action="store_true", help="skip entries with uncommitted changes (to retry only the failures)"
    )
    args = parser.parse_args()

    hash_key = os.getenv("ARTICLE_HASH_KEY")
    if not hash_key:
        sys.exit("ARTICLE_HASH_KEY is not set; it's needed to match entries to their source URLs.")

    cutoff = args.since.replace("-", "")
    files = sorted(
        f for f in os.listdir(pipeline.NEWS_OUTPUT_DIR) if f.endswith(".json") and f != "index.json" and f[:8] >= cutoff
    )
    if args.only:
        files = [f for f in files if any(o in f for o in args.only)]
    if args.skip_modified:
        modified = subprocess.run(
            ["git", "diff", "--name-only", "--", pipeline.NEWS_OUTPUT_DIR], capture_output=True, text=True, check=True
        ).stdout.split()
        files = [f for f in files if not any(m.endswith(f) for m in modified)]
    titles = titles_from_runs(f"{args.since}T00:00:00Z")
    rss = rss_links()
    print(f"{len(files)} entries since {args.since}; {len(titles)} headlines in run logs; {len(rss)} RSS items.")

    def resolve(filename):
        path = os.path.join(pipeline.NEWS_OUTPUT_DIR, filename)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        title, _ = titles.get(filename[16:-5], (None, None))
        link = find_source(title, data.get("article_id"), hash_key, rss) if title and data.get("article_id") else None
        print(f"  {'ok' if link else '??'} {filename[:60]} -> {link}")
        return path, data, title, link

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        resolved = list(ex.map(resolve, files))
    todo = [r for r in resolved if r[3]]
    print(f"\n{len(todo)} of {len(files)} matched to their source by hash.")
    if args.dry_run:
        return

    def regenerate(item):
        path, data, title, link = item
        if pipeline.fatal_api_error or pipeline.budget_exceeded:
            return False
        article = pipeline.build_article(title, "", link, None, data.get("section"))
        if pipeline.is_skipped_topic(article["content"]):
            print(f"  keeping '{title}' as is (topic we don't convert)")
            return False
        result = pipeline.convert_to_emojipasta(article["content"], title)
        if not result:
            return False
        data.update(headline=result["headline"], text=result["text"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        done = sum(ex.map(regenerate, todo))
    print(f"\nRegenerated {done} of {len(todo)}. Grok cost: ${pipeline.run_cost_usd:.4f} (cap ${pipeline.MAX_RUN_COST_USD})")
    if pipeline.fatal_api_error or pipeline.budget_exceeded:
        sys.exit("ERROR: stopped early (API error or MAX_RUN_COST_USD cap).")


if __name__ == "__main__":
    main()
