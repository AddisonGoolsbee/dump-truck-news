import io
import os
import sys
import json
import re
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from PIL import Image

from xai_sdk import Client
from xai_sdk.chat import user, system

from utils import generate_thumbnail

NUM_ARTICLES_PER_SECTION = 1
MAX_ARTICLE_CHARS = 8000  # the whole article is sent in one call; BBC pieces are ~4-8k chars
MAX_IMAGE_GEN_ATTEMPTS = 3

EMOJI_PATTERN = re.compile(
    r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF☀-➿⬀-⯿️‍⃣]+"
)


def emoji_density(text: str) -> float:
    """Emoji-clusters per 100 characters (a cluster = one or more adjacent emoji, i.e. one 'attachment point')."""
    if not text:
        return 0.0
    return len(EMOJI_PATTERN.findall(text)) / len(text) * 100


MAX_CAPS_RATIO = 0.50


def caps_ratio(text: str) -> float:
    """Fraction of alphabetic words that are ALL CAPS. Independent axis from emoji density."""
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if not words:
        return 0.0
    caps = [w for w in words if w.isupper() and len(w) > 1]
    return len(caps) / len(words)


# Generic "AI slop" reaction-face emoji — easy to reach for as filler, but overusing them is what
# makes dense emoji read as mechanical rather than witty. Real emojipasta leans on concrete/literal/
# pun emoji (objects, animals, food, tools) tied to a specific word, not a recycled hype-face.
SLOP_EMOJI = {"😤", "😩", "🥵", "😳", "🔥", "💯", "🙏", "😭", "💀", "🤯", "✨", "😏"}
MAX_SLOP_RATIO = 0.15


def slop_ratio(text: str) -> float:
    """Fraction of all emoji instances that come from the generic reaction-face 'slop' set."""
    clusters = EMOJI_PATTERN.findall(text)
    if not clusters:
        return 0.0
    slop = sum(1 for c in clusters if c in SLOP_EMOJI)
    return slop / len(clusters)

# Thumbnail generation costs ~$0.04/image via OpenAI; keep off until we want to pay for it again.
ENABLE_THUMBNAILS = os.getenv("ENABLE_THUMBNAILS", "false").lower() in ("1", "true", "yes")

SECTIONS = [
    {"name": "US & Canada", "rss": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"},
    {"name": "World", "rss": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Business", "rss": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "Technology", "rss": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
    {"name": "Entertainment", "rss": "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"},
]

# Load environment variables from .env file in the backend directory
BASE_DIR = os.path.dirname(__file__)
NEWS_OUTPUT_DIR = os.path.join(BASE_DIR, "..", "frontend", "public", "news")
NEWS_THUMBNAILS_DIR = os.path.join(BASE_DIR, "..", "frontend", "public", "thumbnails")

env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)

THUMBNAIL_SIZE = (384, 384)
WEBP_QUALITY = 80


def optimize_and_save_thumbnail(raw_image_bytes: bytes, output_path: str):
    """Resize to 2x display size and save as WebP for fast loading."""
    img = Image.open(io.BytesIO(raw_image_bytes))
    img = img.resize(THUMBNAIL_SIZE, Image.LANCZOS)
    img.save(output_path, "WEBP", quality=WEBP_QUALITY)


def hash_article_id(raw_id: str, secret: str) -> str:
    payload = f"{secret}:{raw_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_recent_article_hashes(days: int = 7) -> set[str]:
    if not os.path.isdir(NEWS_OUTPUT_DIR):
        return set()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    hashes: set[str] = set()

    for filename in os.listdir(NEWS_OUTPUT_DIR):
        if not filename.endswith(".json") or filename == "index.json":
            continue

        filepath = os.path.join(NEWS_OUTPUT_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            hashed_id = data.get("article_id")
            date_str = data.get("date")
            if not hashed_id or not date_str:
                continue

            try:
                dt = datetime.fromisoformat(date_str)
            except ValueError:
                continue

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt >= cutoff:
                hashes.add(hashed_id)
        except Exception as exc:
            print(f"Skipped reading {filepath}: {exc}")

    return hashes


def fetch_news_articles(rss_url, section_name, num_articles=1):
    """
    Fetch the top news articles from a BBC RSS feed for a given section.
    Returns a list of article data dictionaries with title, description, link, and content.
    """
    response = requests.get(rss_url)
    response.raise_for_status()

    # Parse XML
    root = ET.fromstring(response.content)

    # Find all items
    items = root.findall(".//item")
    if not items:
        raise ValueError("No articles found in RSS feed")

    articles = []
    for i, item in enumerate(items[:num_articles]):
        title = item.find("title").text
        description = item.find("description").text
        link = item.find("link").text
        guid_text = item.find("guid").text if item.find("guid") is not None else None
        article_id = guid_text.split("#")[0] if guid_text else None

        print(f"Fetching article {i+1}/{num_articles} [{section_name}]: {title}")
        articles.append(build_article(title, description, link, article_id, section_name))

    return articles


def fetch_article_body(link: str) -> str:
    """Fetch a BBC article page and return its body paragraphs joined by blank lines ('' if none found)."""
    article_response = requests.get(link)
    article_response.raise_for_status()

    # Parse the article page to extract content
    soup = BeautifulSoup(article_response.text, "html.parser")

    # BBC articles use specific tags for content
    article_paragraphs = []

    # Try to find article body paragraphs
    article_body = soup.find("article")
    if article_body:
        paragraphs = article_body.find_all("p")
        article_paragraphs = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]

    # Fallback: try data-component="text-block"
    if not article_paragraphs:
        text_blocks = soup.find_all(attrs={"data-component": "text-block"})
        article_paragraphs = [block.get_text().strip() for block in text_blocks if block.get_text().strip()]

    return "\n\n".join(article_paragraphs)


def build_article(title, description, link, article_id, section_name):
    """Assemble the article dict the pipeline consumes, fetching the full page body when possible."""
    description = description or ""
    try:
        full_article = fetch_article_body(link) or description
    except Exception as e:
        print(f"Error fetching article '{title}': {e}")
        # Fall back to basic info even if full content fetch fails
        full_article = ""

    article_text = f"""Title: {title}

{description}

{full_article}""".rstrip() + "\n"

    return {
        "title": title,
        "description": description,
        "link": link,
        "content": article_text,
        "article_id": article_id,
        "section": section_name,
    }


class ConversionFailed(Exception):
    """Raised when an article could not be converted to emojipasta (as opposed to being skipped as a duplicate)."""


def process_single_article(article_data, hash_key, known_hashes, hashes_lock, timestamp=None):
    """
    Process a single article: convert to emojipasta and save to JSON.
    Returns the filename of the saved JSON file, or None if skipped as a duplicate.
    Raises ConversionFailed if the article could not be converted.
    `timestamp` overrides the publish time (used when backfilling missed runs).
    """
    article_text = article_data["content"]
    original_title = article_data["title"]
    raw_article_id = article_data.get("article_id")

    hashed_id = None
    if raw_article_id and hash_key:
        hashed_id = hash_article_id(raw_article_id, hash_key)
        with hashes_lock:
            if hashed_id in known_hashes:
                print(f"Skipping '{original_title}' (duplicate article hash).")
                return None
            # Reserve the hash immediately (not after processing) so two articles
            # with the same id running concurrently can't both slip past the check.
            known_hashes.add(hashed_id)

    print(f"Converting article to emojipasta: {original_title}")

    # Convert to emojipasta
    print(f"  > Sending text to Grok for conversion... ({original_title})")
    emojipasta_data = convert_to_emojipasta(article_text, original_title)
    if not emojipasta_data:
        print(f"Skipping '{original_title}' (emojipasta conversion failed).")
        raise ConversionFailed(original_title)

    if hashed_id:
        emojipasta_data["article_id"] = hashed_id

    emojipasta_data["section"] = article_data.get("section")

    timestamp = timestamp or datetime.now(timezone.utc)
    emojipasta_data["date"] = str(timestamp)
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    safe_title = "".join(c for c in original_title if c.isalnum() or c in (" ", "-", "_")).rstrip()
    safe_title = safe_title.replace(" ", "_")[:50]
    safe_title = f"{timestamp_str}_{safe_title}"

    if ENABLE_THUMBNAILS:
        print(f"  > Generating thumbnail image...")
        image_filename = None
        for attempt in range(MAX_IMAGE_GEN_ATTEMPTS):
            try:
                image = generate_thumbnail(article_data["content"], emojipasta_data["headline"])
                if image:
                    os.makedirs(NEWS_THUMBNAILS_DIR, exist_ok=True)
                    image_filename = os.path.join(NEWS_THUMBNAILS_DIR, f"{safe_title}.webp")
                    optimize_and_save_thumbnail(image, image_filename)
                    break
            except Exception as e:
                print(f"Image generation attempt {attempt + 1} failed: {e}")
                image = None

        if image_filename:
            emojipasta_data["image"] = os.path.basename(image_filename)
            print(f"  > Image saved: {os.path.basename(image_filename)}")
        else:
            print(f"  > Skipping thumbnail (failed after {MAX_IMAGE_GEN_ATTEMPTS} attempts). Article will be saved without image.")

    # Save to JSON
    filename = save_emojipasta_json(emojipasta_data, safe_title)

    print(f"Saved: {filename}")
    return filename


# Cost notes (Sept 2026): the Aug-22 rewrite used a reasoning model with up to 19 calls per article and burned
# ~45c/article, which drained the xAI credits within days. This is the cheap version: ONE non-reasoning call
# per article (~0.5-1c) with a compact prompt, plus a hard per-run spending cap so a regression can't do that again.
MODEL = os.getenv("XAI_MODEL", "grok-4.20-non-reasoning")
MAX_RUN_COST_USD = float(os.getenv("MAX_RUN_COST_USD", "0.10"))
# One retry at most when the output comes back sparse; each attempt is ~0.5c, and the retries were the old cost sink.
MAX_ATTEMPTS = 2
MIN_EMOJI_PER_100_CHARS = 5.0

STYLE_RULES = """
You are an r/emojipasta poster rewriting a real news article as unhinged "emojipasta": internet copypasta that reads like it was typed by someone way too invested, at 2am, mid rant. Respond with valid JSON only.

Example of the target style (study the density, the single-emoji attachments, the caps ratio, the innuendo):
"SENATE 🏛️ FINALLY 🏁 busts 💦 a NUT 🥜 on that 2 TRILLION 💰 dollar 💵 infraSTUDcture 🍆 bill 📜 after a MARATHON 🏃‍♂️ 15-hour 🕐 session that left 😵‍💫 everyone 🫠 DRIPPING 💧 with EXHAUSTION 🥵!! Majority 👑 Leader 👔 Dale 🍑 Whitfield, affectionately 💅 known 🏷️ as DILF 🐺 Dale to the interns 👀, STROKED 👐 every senator's 🧑‍⚖️ ego 🥴 one by one 🔂 until they FOLDED 🙇‍♂️ like a cheap 💸 lawn chair 🪑, finally SEALING 💍 the deal 🤝 at 3am 🌙 with a 62-38 vote 🗳️!! "We got RAILED 💦 by the process ⚙️," admitted 🎙️ Senator Beth Carrow, "but honestly 🤭? Kinda into it 😳.""

Rules:
1. DENSITY: roughly one emoji every 1-3 words, all the way to the LAST sentence — do not taper off. Vary the gaps so it doesn't read like a metronome.
2. SINGLES: about 90% of attachment points are exactly ONE emoji. At most one 2-emoji stack per paragraph, for the biggest punchline.
3. PICK LITERAL/PUN EMOJI tied to the specific word next to them (objects, animals, food, tools, weather, body parts). Do not lean on generic reaction faces (😤 😩 🥵 😳 🔥 💯 🙏 😭 💀 🤯 ✨ 😏) as filler; don't repeat any one emoji more than ~3 times per paragraph.
4. CAPS: a third to half of words in caps for emphasis, never all of them — small words stay lowercase.
5. PUNCTUATION: full normal sentences with commas, periods, quotes and "!!". Emoji are inserted between words, never replacing punctuation.
6. INNUENDO in every paragraph: word-mangling swaps (infraSTUDcture, legiSLAYtion, approPORNiate), reframing the mundane action as a horny encounter (negotiating = edging, a deal closing = the climax, a long session = getting railed), a running thirsty nickname for ONE named person or entity (e.g. "Wab Kinew" -> "Wab Daddy") used throughout, and the odd suggestive aside from a fictional bystander. Keep it innuendo, not explicit.
7. Light meme slang (bro, cooked, down bad, unc, built different) — sparingly.
8. FACTS: every claim must trace back to the article. Keep names, numbers and quotes accurate. Don't invent plot details; the comedy is in the voice.

Write a headline (under 10 words, normal capitalization with a couple of CAPS bursts and 2-4 emoji) and 5-6 paragraphs of 80-130 words each, covering the article's facts in order, separated by blank lines.

Output JSON exactly as:
{
    "headline": "...",
    "text": "paragraph 1\\n\\nparagraph 2\\n\\n..."
}
"""


# Errors that retrying can't fix (exhausted credits, bad key). Once one is seen, every subsequent call
# would fail the same way, so we stop retrying and make the whole run exit non-zero at the end.
FATAL_API_ERROR_MARKERS = ("PERMISSION_DENIED", "UNAUTHENTICATED", "spending limit", "available credits", "Incorrect API key")
fatal_api_error: str | None = None
budget_exceeded = False
run_cost_usd = 0.0
run_tokens = {"prompt": 0, "completion": 0}
_state_lock = Lock()


def _api_error_summary(exc: Exception) -> str:
    msg = str(exc)
    m = re.search(r'details = "(.*?)"', msg)
    return m.group(1) if m else msg.strip().splitlines()[0][:300]


def _record_usage(response) -> float:
    """Add this response's cost/tokens to the run totals; returns the cost of this call (0 if unreported)."""
    global run_cost_usd, budget_exceeded
    cost = response.cost_usd or 0.0
    usage = response.usage
    with _state_lock:
        run_cost_usd += cost
        run_tokens["prompt"] += getattr(usage, "prompt_tokens", 0)
        run_tokens["completion"] += getattr(usage, "completion_tokens", 0)
        if run_cost_usd >= MAX_RUN_COST_USD and not budget_exceeded:
            budget_exceeded = True
            print(f"    BUDGET: run cost ${run_cost_usd:.4f} reached MAX_RUN_COST_USD=${MAX_RUN_COST_USD}; no further Grok calls.")
    return cost


def _chat_json(client, system_prompt, user_prompt):
    """One JSON-mode chat call with a retry on parse failure. Returns (parsed dict or None, cost in USD)."""
    global fatal_api_error
    cost = 0.0
    for attempt in range(2):
        if fatal_api_error or budget_exceeded:
            return None, cost
        try:
            chat = client.chat.create(model=MODEL)
            chat.append(system(system_prompt))
            note = "" if attempt == 0 else " Your previous reply was not valid JSON; reply with only the JSON object."
            chat.append(user(user_prompt + note))
            response = chat.sample()
            cost += _record_usage(response)
            content = response.content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
            return json.loads(content), cost
        except json.JSONDecodeError as e:
            print(f"    JSON parse failed (attempt {attempt + 1}): {e}")
        except Exception as e:
            if any(marker in str(e) for marker in FATAL_API_ERROR_MARKERS):
                with _state_lock:
                    if not fatal_api_error:
                        fatal_api_error = _api_error_summary(e)
                        print(f"    FATAL xAI API error (will not retry): {fatal_api_error}")
                return None, cost
            print(f"    Unexpected error (attempt {attempt + 1}): {e}")
    return None, cost


def convert_to_emojipasta(article_text, original_title):
    """
    One Grok call: headline + full emojipasta text. Returns {"headline", "text"} or None.
    Density/caps/slop are measured and logged but not retried — retries were the main cost driver.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable is not set")

    client = Client(api_key=api_key, timeout=600)

    if len(article_text) > MAX_ARTICLE_CHARS:
        truncated = article_text[:MAX_ARTICLE_CHARS]
        last_break = truncated.rfind("\n\n")
        article_text = (truncated[:last_break] if last_break > 0 else truncated) + "\n\n[TRUNCATED]"

    base_prompt = (
        f"Article title: {original_title}\n\nArticle content:\n{article_text}\n\n"
        f"Now write the emojipasta. HARD REQUIREMENT: an emoji after every 1-3 words, in EVERY sentence of EVERY "
        f"paragraph — that is 35-50 emoji per paragraph, {MIN_EMOJI_PER_100_CHARS:.0f}+ emoji per 100 characters. "
        f"A paragraph with only a handful of emoji is a failure. Output only the JSON described."
    )
    total_cost = 0.0
    best = None
    feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        result, cost = _chat_json(client, STYLE_RULES, base_prompt + feedback)
        total_cost += cost
        if not result or not isinstance(result.get("text"), str) or not result.get("headline"):
            continue
        text = result["text"].strip()
        density = emoji_density(text)
        if best is None or density > best[0]:
            best = (density, result["headline"], text)
        if density >= MIN_EMOJI_PER_100_CHARS:
            break
        feedback = (
            f"\n\nYour previous attempt had only {density:.1f} emoji per 100 characters — far too sparse, it did not "
            f"read as emojipasta at all. Rewrite it with an emoji attached after every 1-3 words throughout, "
            f"including the final paragraph. Keep the facts the same."
        )
    if best is None:
        print(f"  > Conversion returned no usable JSON. Aborting this article. (cost ${total_cost:.4f})")
        return None
    cost = total_cost
    _, headline, text = best
    result = {"headline": headline, "text": text}
    print(
        f"  > density {emoji_density(text):.1f}/100ch, caps {caps_ratio(text) * 100:.0f}%, "
        f"slop {slop_ratio(text) * 100:.0f}%, {len(text)} chars, cost ${cost:.4f} ({original_title[:50]})"
    )
    return {"headline": result["headline"], "text": text}


def save_emojipasta_json(emojipasta_data, safe_title):
    """
    Save the emojipasta data as JSON with metadata.
    """

    # Construct absolute path to frontend/public directory
    os.makedirs(NEWS_OUTPUT_DIR, exist_ok=True)

    filename = os.path.join(NEWS_OUTPUT_DIR, f"{safe_title}.json")

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(emojipasta_data, f, ensure_ascii=False, indent=2)

    return filename


def main():
    hash_key = os.getenv("ARTICLE_HASH_KEY")
    if not hash_key:
        hash_key = "demo-secret-change-me-041f6a73"
        print("WARNING: ARTICLE_HASH_KEY not set. Using demo key; please update your .env.")

    recent_hashes = load_recent_article_hashes()
    print(f"Loaded {len(recent_hashes)} recent article hashes for deduping.")
    hashes_lock = Lock()
    print(f"Fetching top {NUM_ARTICLES_PER_SECTION} article(s) from each of {len(SECTIONS)} sections...")

    # Fetch the top article(s) from every section's RSS feed
    articles = []
    for section in SECTIONS:
        try:
            articles.extend(fetch_news_articles(section["rss"], section["name"], NUM_ARTICLES_PER_SECTION))
        except Exception as e:
            print(f"Error fetching section '{section['name']}': {e}")
    print(f"Fetched {len(articles)} articles\n")

    # Process articles in parallel
    print("Converting articles to emojipasta with Grok (processing in parallel)...")

    saved_files = []
    failed = 0
    with ThreadPoolExecutor(max_workers=min(len(articles), 5) or 1) as executor:  # Limit to 5 concurrent requests
        # Submit all tasks
        future_to_article = {
            executor.submit(process_single_article, article, hash_key, recent_hashes, hashes_lock): article
            for article in articles
        }

        # Process completed tasks as they finish
        for future in as_completed(future_to_article):
            article = future_to_article[future]
            try:
                filename = future.result()
                if filename:
                    saved_files.append(filename)
            except ConversionFailed:
                failed += 1
            except Exception as exc:
                failed += 1
                print(f"Article '{article['title']}' generated an exception: {exc}")

    print(f"\nConversion complete! Saved {len(saved_files)} of {len(articles)} articles ({failed} failed).")
    print(
        f"Grok cost this run: ${run_cost_usd:.4f} "
        f"({run_tokens['prompt']} prompt + {run_tokens['completion']} completion tokens, cap ${MAX_RUN_COST_USD})"
    )
    print("Saved files:")
    for filename in saved_files:
        print(f"  - {filename}")

    if saved_files:
        print("\n--- Sample Preview (first article) ---")
        try:
            with open(saved_files[0], "r", encoding="utf-8") as f:
                sample_data = json.load(f)
                print(f"Headline: {sample_data['headline']}")
                print(
                    f"Text preview: {sample_data['text'][:500]}..."
                    if len(sample_data["text"]) > 500
                    else f"Text: {sample_data['text']}"
                )
        except Exception as e:
            print(f"Could not load preview: {e}")

    # A run that fetched articles but converted none of them is broken, not "nothing new" — fail loudly so the
    # GitHub Action goes red instead of silently succeeding with an empty commit step.
    if fatal_api_error:
        print(f"\nERROR: xAI API rejected requests: {fatal_api_error}", file=sys.stderr)
        sys.exit(1)
    if budget_exceeded:
        print(f"\nERROR: run cost ${run_cost_usd:.4f} hit MAX_RUN_COST_USD=${MAX_RUN_COST_USD}; check for a cost regression.", file=sys.stderr)
        sys.exit(1)
    if failed and not saved_files:
        print(f"\nERROR: all {failed} conversion attempt(s) failed; nothing was saved.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
