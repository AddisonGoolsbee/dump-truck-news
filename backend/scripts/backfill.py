#!/usr/bin/env python3
"""
Backfill emojipasta entries for articles the "Daily Update" GitHub Action fetched but never converted
(for example while the xAI account was out of credits and every Grok call was rejected).

Two steps:

  collect  Scan recent workflow runs with the `gh` CLI, pull every headline each run fetched, drop the
           ones that were actually published, resolve each headline to its BBC article URL, and write a
           manifest JSON. Entries whose URL could not be confirmed are marked "verified": false.

  run      Convert the manifest's articles with the normal pipeline (main.py), stamped with the time of
           the run that originally fetched them so they slot into the feed where they should have appeared.
           Duplicates are skipped by article hash against the entire published history.

Usage (from backend/, with the usual .env in place):

  python scripts/backfill.py collect --since 2026-08-26T12:00:00Z --out scripts/backfill_manifest.json
  python scripts/backfill.py run scripts/backfill_manifest.json --dry-run
  python scripts/backfill.py run scripts/backfill_manifest.json [--limit N] [--include-unverified]

Afterwards commit frontend/public/news and push; the Pages deploy picks it up.
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock

import requests
from bs4 import BeautifulSoup

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import main as pipeline  # noqa: E402

WORKFLOW = "daily-update.yml"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; emoji-news-backfill)"}
ARTICLE_URL_RE = re.compile(r"https://www\.bbc\.co\.uk/news/(articles|videos)/[a-z0-9]+")
STOPWORDS = set(
    "a an the of to in on at for and or but is are was were be been as by with from after over into says say "
    "said it its his her their this that these those he she they we you i not no more than about amid up out "
    "new first".split()
)
MIN_MATCH_SCORE = 0.6

# ---------------------------------------------------------------- collect


def safe_title(title: str) -> str:
    """Same filename-safe transform main.py applies, so we can match log titles to files on disk."""
    s = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).rstrip()
    return s.replace(" ", "_")[:50]


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def list_runs(since: str) -> list[tuple[str, str]]:
    raw = gh("run", "list", "--workflow", WORKFLOW, "--limit", "200", "--json", "databaseId,createdAt")
    runs = [(str(r["databaseId"]), r["createdAt"]) for r in json.loads(raw) if r["createdAt"] >= since]
    return sorted(runs, key=lambda r: r[1])


def scan_run(run_id: str) -> dict:
    """Return the headlines a run fetched, plus the ones it saved or skipped as duplicates."""
    log = gh("run", "view", run_id, "--log")
    fetched, saved, dup = [], set(), set()
    for line in log.splitlines():
        line = re.sub(r"^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z ", "", line)
        m = re.match(r"Fetching article \d+/\d+ \[(.*?)\]: (.*)$", line)
        if m:
            fetched.append((m.group(1), m.group(2).strip()))
            continue
        m = re.match(r"Saved: .*?/\d{8}_\d{6}_(.*)\.json$", line)
        if m:
            saved.add(m.group(1))
            continue
        m = re.match(r"Skipping '(.*)' \(duplicate article hash\)", line)
        if m:
            dup.add(m.group(1))
    return {"fetched": fetched, "saved": saved, "dup": dup}


def content_tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return [t for t in re.sub(r"[^a-z0-9 ]+", " ", text).split() if t not in STOPWORDS and len(t) > 1]


def match_score(title: str, candidate: str) -> float:
    t, c = content_tokens(title), content_tokens(candidate)
    if not t:
        return 0.0
    overlap = sum(1 for x in t if x in c) / len(t)
    seq = difflib.SequenceMatcher(None, " ".join(t), " ".join(c)).ratio()
    return round(max(overlap, seq), 3)


def bbc_search(query: str) -> list[tuple[str, str]]:
    r = requests.get("https://www.bbc.co.uk/search", params={"q": query}, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a[href*='/news/']"):
        href = a["href"].split("?")[0]
        if ARTICLE_URL_RE.fullmatch(href):
            out.append((href, a.get_text(" ", strip=True)))
    return out


def ddg_search(title: str) -> list[str]:
    r = requests.get(
        "https://html.duckduckgo.com/html/", params={"q": f'site:bbc.co.uk "{title}"'}, headers=HEADERS, timeout=30
    )
    return [f"https://{m}" for m in dict.fromkeys(re.findall(r"www\.bbc\.co\.uk/news/(?:articles|videos)/[a-z0-9]+", r.text))]


def page_overlap(title: str, link: str) -> float:
    """How much of the headline's vocabulary shows up in the article page (headline + opening body)."""
    try:
        r = requests.get(link, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:title")
        body = soup.find("article") or soup
        text = (og["content"] if og else "") + " " + body.get_text(" ", strip=True)[:2500]
        t = content_tokens(title)
        page = set(content_tokens(text))
        return round(sum(1 for x in t if x in page) / len(t), 3) if t else 0.0
    except Exception:
        return 0.0


def resolve(entry: dict) -> dict:
    """Find the BBC URL for a headline. Sets link/article_id/match_score/matched_headline/verified."""
    title = entry["title"]
    candidates: list[tuple[str, str]] = []
    try:
        candidates += bbc_search(title)
        if not candidates or max(match_score(title, h) for _, h in candidates) < MIN_MATCH_SCORE:
            candidates += bbc_search(" ".join(content_tokens(title)[:8]))
        if not candidates or max(match_score(title, h) for _, h in candidates) < MIN_MATCH_SCORE:
            candidates += [(href, "") for href in ddg_search(title)[:3]]
    except Exception as exc:
        print(f"  search failed for '{title}': {exc}")

    scored = []
    for href, headline in dict.fromkeys(candidates):
        score = match_score(title, headline) if headline else 0.0
        if score < MIN_MATCH_SCORE:
            score = max(score, page_overlap(title, href))
        scored.append((score, href, headline))
    best = max(scored, default=None)
    if best:
        score, href, headline = best
        entry.update(link=href, article_id=href, match_score=score, matched_headline=headline or None, verified=score >= MIN_MATCH_SCORE)
    else:
        entry.update(link=None, article_id=None, match_score=0.0, matched_headline=None, verified=False)
    flag = "ok " if entry["verified"] else "?? "
    print(f"  {flag}{entry['match_score']:.2f} {title[:70]!r} -> {entry['link']}")
    return entry


def collect(args) -> None:
    runs = list_runs(args.since)
    print(f"Scanning {len(runs)} runs since {args.since}...")
    existing = os.listdir(pipeline.NEWS_OUTPUT_DIR)
    entries: dict[str, dict] = {}
    for run_id, created in runs:
        info = scan_run(run_id)
        for section, title in info["fetched"]:
            if title in entries or title in info["dup"] or safe_title(title) in info["saved"]:
                continue
            if any(f.endswith(safe_title(title) + ".json") for f in existing):
                continue
            entries[title] = {"title": title, "section": section, "timestamp": created, "run_id": run_id}
    print(f"{len(entries)} unpublished headlines to resolve...")

    with ThreadPoolExecutor(max_workers=4) as ex:
        resolved = list(ex.map(resolve, entries.values()))

    # The same article often shows up under several headlines across runs; keep the earliest per URL.
    by_link: dict[str, dict] = {}
    unresolved = []
    for e in sorted(resolved, key=lambda e: e["timestamp"]):
        if not e["link"]:
            unresolved.append(e)
        elif e["link"] not in by_link:
            by_link[e["link"]] = e
    manifest = list(by_link.values()) + unresolved
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    verified = sum(1 for e in manifest if e["verified"])
    print(f"\nWrote {args.out}: {len(manifest)} entries ({verified} verified, {len(manifest) - verified} need review).")


# ---------------------------------------------------------------- run


def run(args) -> None:
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    todo = [e for e in manifest if e.get("link") and (e.get("verified") or args.include_unverified)]
    if args.limit:
        todo = todo[: args.limit]
    skipped = len(manifest) - len(todo)
    print(f"{len(todo)} entries to backfill ({skipped} skipped: unresolved or unverified).")

    hash_key = os.getenv("ARTICLE_HASH_KEY")
    if not hash_key:
        sys.exit("ARTICLE_HASH_KEY is not set; refusing to backfill with mismatched dedupe hashes.")
    known = pipeline.load_recent_article_hashes(days=36500)  # whole history, not just the last week
    lock = Lock()

    def work(entry):
        if pipeline.fatal_api_error:
            return None
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if args.dry_run:
            print(f"  would backfill @{ts:%Y-%m-%d %H:%M} [{entry['section']}] {entry['title']}  <{entry['link']}>")
            return "dry-run"
        article = pipeline.build_article(entry["title"], "", entry["link"], entry["article_id"], entry["section"])
        return pipeline.process_single_article(article, hash_key, known, lock, timestamp=ts)

    saved, failed = [], 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(work, e): e for e in todo}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result:
                    saved.append(result)
            except pipeline.ConversionFailed:
                failed += 1
            except Exception as exc:
                failed += 1
                print(f"  '{futures[fut]['title']}' raised: {exc}")

    verb = "would be backfilled" if args.dry_run else "saved"
    print(f"\nBackfill done: {len(saved)} {verb}, {failed} failed, {len(todo) - len(saved) - failed} skipped as duplicates.")
    if pipeline.fatal_api_error:
        sys.exit(f"ERROR: xAI API rejected requests: {pipeline.fatal_api_error}")
    if failed and not saved:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="build a manifest of missed articles from workflow run logs")
    c.add_argument("--since", required=True, help="ISO timestamp; only runs created at/after this are scanned")
    c.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "backfill_manifest.json"))
    c.set_defaults(func=collect)
    r = sub.add_parser("run", help="generate emojipasta for every entry in a manifest")
    r.add_argument("manifest")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--include-unverified", action="store_true", help="also process entries whose URL match is weak")
    r.set_defaults(func=run)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
