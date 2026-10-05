import io
import os
import sys
import json
import re
import hashlib
import random
import difflib
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


# One emoji: a flag pair, or a base emoji plus any skin-tone/variation/ZWJ/keycap continuations.
SINGLE_EMOJI_PATTERN = re.compile(
    r"(?:[\U0001F1E6-\U0001F1FF]{2}|[\U0001F300-\U0001FAFF☀-➿⬀-⯿]"
    r"(?:[\U0001F3FB-\U0001F3FF]|️|‍[\U0001F300-\U0001FAFF☀-➿⬀-⯿]️?|⃣)*)"
)


def _words(text: str) -> list[str]:
    return [w for w in EMOJI_PATTERN.sub(" ", text).split() if any(c.isalnum() for c in w)]


def emoji_density(text: str) -> float:
    """Emoji per 100 words. Real r/emojipasta posts have a median of ~58 (Oct 2026 sample of 150 posts)."""
    words = _words(text)
    if not words:
        return 0.0
    return len(SINGLE_EMOJI_PATTERN.findall(text)) / len(words) * 100


def stack_ratio(text: str) -> float:
    """Fraction of emoji attachment points that are stacks of 2+ emoji. Real r/emojipasta is ~50%."""
    clusters = [c for c in EMOJI_PATTERN.findall(text) if SINGLE_EMOJI_PATTERN.search(c)]
    if not clusters:
        return 0.0
    return sum(1 for c in clusters if len(SINGLE_EMOJI_PATTERN.findall(c)) >= 2) / len(clusters)


def emoji_variety(text: str) -> float:
    """Distinct emoji / total emoji. Real r/emojipasta is ~0.67; spammy metronome output drops toward 0.3."""
    emoji = SINGLE_EMOJI_PATTERN.findall(text)
    return len(set(emoji)) / len(emoji) if emoji else 0.0


def tail_density(text: str) -> float:
    """emoji_density of the last third of the text, to catch the model running out of steam."""
    return emoji_density(text[-(len(text) // 3):])


def caps_ratio(text: str) -> float:
    """Fraction of alphabetic words that are ALL CAPS. Independent axis from emoji density."""
    words = [w for w in _words(text) if any(c.isalpha() for c in w)]
    if not words:
        return 0.0
    caps = [w for w in words if w.isupper() and len(w) > 1]
    return len(caps) / len(words)


# Sexual markers; a post containing any of these must not mention children.
SEXUAL_PATTERN = re.compile(
    r"🍆|💦|🍑|👅|💋|🥵|\b(?:(?:slut|whore|daddy|dilf|thicc|orgy|orgies|horny|thirst|goon|cumm)\w*|hoes?|cum|loads?)\b",
    re.IGNORECASE,
)

# The prompt rule alone wasn't reliable at keeping children out of the posts, so it's also checked in code.
MINOR_PATTERN = re.compile(
    r"(?<!\u200d)(?:🧒|👶|👧|👦)(?!\u200d)|🚸|\b(?:child|children|kids?|teen\w*|schools?|pupils?|minors?|under[- ]?1[0-7]"
    r"|aged? (?:1[0-7]|[1-9])|since (?:they were|age) \w+)\b",
    re.IGNORECASE,
)
CHILD_EMOJI = {"🧒", "👶", "👧", "👦", "🚸"}

# Stories about sexual violence or children being harmed are skipped rather than turned into emojipasta.
SEXUAL_VIOLENCE_PATTERN = re.compile(
    r"\b(?:rap(?:e|ed|es|ist|ists|ing)|sexual(?:ly)? (?:assault|abus)\w*|molest\w*|p(?:a)?edophil\w*|grooming"
    r"|child abuse|sex(?:ual)? trafficking)\b",
    re.IGNORECASE,
)
CHILD_HARM_PATTERN = re.compile(
    r"\b(?:child|children|kids?|bab(?:y|ies)|toddlers?|infants?|sons?|daughters?|pupils?|schoolchildren|teenagers?)\b"
    r"\W+(?:\w+\W+){0,6}?(?:kill\w*|dead|deaths?|died|murder\w*|injur\w*|abus\w*|shot|stabb\w*|drown\w*|starv\w*)\b"
    r"|\b(?:kill\w*|deaths?|died|murder\w*|injur\w*|abus\w*|shot|stabb\w*|drown\w*|starv\w*)\W+(?:\w+\W+){0,6}?"
    r"(?:child|children|kids?|bab(?:y|ies)|toddlers?|infants?|sons?|daughters?|pupils?|schoolchildren|teenagers?)\b",
    re.IGNORECASE,
)
LEDE_CHARS = 1200  # title + description + the first couple of paragraphs


def is_skipped_topic(article_text: str) -> bool:
    """Sexual violence, or children being killed/hurt, in the headline or opening paragraphs."""
    lede = article_text[:LEDE_CHARS]
    return bool(SEXUAL_VIOLENCE_PATTERN.search(lede) or CHILD_HARM_PATTERN.search(lede))


NICKNAME_CHANCE = 0.35  # a thirsty nickname in every post got monotonous

# Thumbnail generation costs ~$0.04/image via OpenAI; keep off until we want to pay for it again.
ENABLE_THUMBNAILS = os.getenv("ENABLE_THUMBNAILS", "false").lower() in ("1", "true", "yes")

SECTIONS = [
    {"name": "US & Canada", "rss": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"},
    {"name": "World", "rss": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Technology", "rss": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
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
    Returns the filename of the saved JSON file, or None if skipped (duplicate, or a topic we don't convert).
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

    if is_skipped_topic(article_text):
        print(f"Skipping '{original_title}' (sexual violence or harm to children).")
        return None

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
# Targets measured from 150 r/emojipasta posts (Oct 2026): median ~120 words, ~58 emoji per 100 words,
# ~half of emoji spots are stacks of 2+, ~25% of words in caps, and no density drop-off at the end.
MIN_EMOJI_PER_100_WORDS = 40.0
MAX_WORDS = 280
MAX_EMOJI_PER_100_WORDS = 85.0
MIN_EMOJI_VARIETY = 0.45

STYLE_RULES = """
You write posts for r/emojipasta: you turn a real news article into ONE short, unhinged, thirsty emojipasta copypasta. Respond with valid JSON only.

Two examples of the exact style (copy the style, never these facts, nicknames, groups or punchlines):

METAPHOR: road building as sex: laying pipe, filling holes, steamrolling, tight joints
PUNS: infraSTUDcture, SenatWHORE, BUSTpartisan, CUMmittee
HEADLINE: SENATE 🏛️💦 LAYS PIPE 🔧🍆 on $2 TRILLION infraSTUDcture BILL 🛣️😩
TEXT:
🚨🏛️ATTENTION all you CAPITOL HILL HOES🏛️🚨 the SENATE 🍑🍑 spent 1️⃣5️⃣ HOURS 🕒😩 LAYING PIPE 🔧🍆 all night 🌙 until it finally SHOT 💦💦 a $2 TRILLION 💰💰 infraSTUDcture bill 📜🍆 out of CUMmittee at 3am ⏰👀‼️ Majority Leader Dale Whitfield aka DILF DALE 🐺😍 kept every SenatWHORE 🧑‍⚖️💋 BENT over their desk 🪑🙇 until they gave it up 6️⃣2️⃣➖3️⃣8️⃣ 🗳️✅ in a sweaty BUSTpartisan finish 🥵 and Senator Beth Carrow 💅 admitted "we got RAILED 🚂💦 by the process" 😳‼️

Now the cash 💸💸 goes DEEP 🕳️👀 into America's POTHOLES 🕳️🍆 so every road 🛣️ gets FILLED, PACKED and STEAMROLLED 🚜💦 nice and SMOOTH 😌🍑 while the BRIDGES 🌉 get their TIGHT little JOINTS 🔩🔩 reinforced 💪‼️ The House 🏠 still has to SWALLOW 😳👄 it next week 📅 so stay HYDRATED 💧💧💧

SEND 📩 this to 🔟 of your THICCEST 🍑 taxpayers 💸
0️⃣ back = your commute stays BUMPY 🚗🕳️😭
5️⃣ back = you get fresh ASPHALT 🛣️😌 by Friday
🔟 back = DILF DALE lays PIPE 🔧🍆 under YOUR street TONIGHT 🌙💦

METAPHOR: chips and hardware as arousal: hardware getting HARD, racks, overclocking, going soft
PUNS: GPUssy, Blackwell → BlackWELL-HUNG, DICKital, HARDware
HEADLINE: NVIDIA 💚 whips out 🍆 $57BN and Wall Street CAN'T TAKE IT 😩📈
TEXT:
📢💚 HEY all you GPU GOONERS 🖥️🤤 NVIDIA just whipped out 🍆👀 its Q3 numbers 📊 and they're HUGE 📏😳 revenue SWELLED 🍆📈 62% to a THICC $57BN 💰🍑 with the data centre RACKS 🖥️🍒 alone pulling $51BN 😩💦 Everyone kept MOANING 😩😩 that the AI BUBBLE 🫧🫧 was about to POP 💥 but DADDY JENSEN 🕶️🧥 slid into his leather 🧥😈 and said the BlackWELL-HUNG 🍆⚫ chips are "OFF THE CHARTS" 📊🚀 so the cloud GPUssy 🖥️🐱 is SOLD OUT 🚫🛒 and the whole DICKital economy 💻💦 is waiting its turn ⏳‼️ Shares got STIFF 🍆📈 4% after hours 🌙 and the Q4 forecast is a $65BN LOAD 🤑💦 so Wall Street 🐂 is getting its PORTFOLIO PLOWED 🚜📈 whether it's ready or not 😳‼️

SEND 📩 this to 6️⃣9️⃣ of your NERDIEST 🤓 chip SLUTS 🖥️💋
0️⃣ back = your HARDware goes SOFT 📉🍆
🔟 back = DADDY JENSEN overclocks YOU ⚡🧥😍

Rules:
1. EVERY story gets the full thirst: wars, deaths, crime, disasters and executions included, no holding back (r/emojipasta does horny pastas about all of it). Aim it at the attackers, the weapons, the politicians, the institutions and the absurdity.
2. LENGTH: 120-200 words TOTAL. One or two short paragraphs, then the chain-letter closer. The facts are only the skeleton: use 3-5 of them and spend the words on the jokes; never pad, never recap the article.
3. DENSITY: about one emoji per two words (50-60 emoji per 100 words) and NEVER more than 3 words in a row without an emoji, from the first line to the last. Roughly half the emoji spots are stacks of 2-3 (🍑🍑, 💦💦💦, 📈🚀), the rest singles. Put each emoji right after the word it illustrates, mostly literal or a pun (POTHOLE 🕳️🍆). Numbers often become keycaps (6️⃣9️⃣, 🔟).
4. OPENER: shout at an audience themed to this story, in the shape you are given with the article.
5. CLOSER: a chain letter in the shape you are given with the article, each line a joke about THIS story's facts.
6. VOICE: run-on, breathless, sentences slamming into each other, ‼️ and !!!, about a third of words in CAPS (the punchy nouns and verbs, not every word).
7. THIRST, and the innuendo is the whole point:
   a) METAPHOR: pick ONE dirty extended metaphor from the story's own world (oil = PUMPING, DRILLING, a fat LOAD of crude; roads = LAYING PIPE, FILLING holes; a museum = TOUCHING the exhibits; a smart ring = FINGERING) and run it through every sentence.
   b) PUNS: 3-5 word-mangling sexual puns built from words IN THIS story (circumference = cirCUMference, Senator = SenatWHORE, diameter = DICKameter, Brexit = BREASTxit), and use every one of them in the text.
   c) Every sentence carries a double entendre. A sentence that is just a fact with emoji and caps is a failure.
   d) Stock words (EDGED, RAILED, THICC, LOAD, DADDY, SLAY, BUSTED, ORGY) at most twice in total; the jokes have to come from THIS story's words.
   🍆💦🍑 and crude innuendo are on; describing actual sex acts is off, and so is any sexual joke within reach of children or anyone under 18 (leave them out of a thirsty post entirely).
8. FACTS: names, numbers and quotes come from the article and stay accurate. Don't invent events. No slurs. Punch up: the jokes target politicians, institutions and the absurdity, never migrants, refugees, religious or ethnic groups, and the chain letter doesn't take a side on contested politics.
9. HEADLINE: under 10 words, CAPS bursts, a pun, 3-5 emoji including one stack.

Output JSON exactly as (plan the metaphor and puns first, then write):
{"metaphor": "...", "puns": ["...", "..."], "headline": "...", "text": "..."}
Use \\n for line breaks inside "text" (blank line between paragraphs, single line breaks between the chain-letter lines).
"""

# Second, cheap pass used when the first draft comes back sparse. A non-reasoning model writes good voice but
# under-emojis, and told to "add more" it either barely changes anything or spams one emoji after every word. So the
# code picks the spots (numbered slots after punchy words in long emoji-free stretches) and the model only chooses
# what goes in each slot, which keeps the density in range and leaves the words untouched.
SLOT_FILL_RULES = """
You pick emoji for an r/emojipasta post. The post has numbered slots like [[3]]. For each slot choose emoji for the word
right before it: a literal match or a pun. ODD-numbered slots get a stack of 2-3 emoji (a combo like 📈🚀 or a repeat
for emphasis like 💦💦💦); EVEN-numbered slots get exactly one. A slot glued straight onto an emoji (like 🔥[[4]]) gets
exactly one extra emoji that goes with that emoji and word. Use lots of different emoji; never repeat what is right
next to the slot. Respond with JSON only, mapping every slot number to its emoji, e.g. for
"POTHOLE [[1]] ... VOTE [[2]] ... SURGED [[3]]": {"1": "🕳️🍆", "2": "🗳️", "3": "📈📈🚀"}
"""
SMALL_WORDS = {
    "a", "an", "the", "to", "of", "and", "or", "but", "in", "on", "at", "for", "with", "by", "from", "as", "is", "are",
    "was", "were", "be", "been", "it", "its", "it's", "his", "her", "their", "our", "your", "my", "this", "that", "so",
    "just", "up", "out", "all", "you", "we", "they", "he", "she", "i", "who", "than", "then", "after", "into", "about",
}
# One opener and one closer shape is picked at random per article; with only the examples to go on, the model opens
# and closes every post the same way. These are the common shapes in r/emojipasta.
OPENER_SHAPES = [
    '"🚨🚨ATTENTION all you <themed group>🚨🚨"',
    '"‼️WAKE UP <themed group>‼️"',
    '"📢 calling ALL <themed group> 📢"',
    '"OMG 😱😱 did you HEAR"',
    '"BREAKING 🚨📰 NEWS for all the <themed group>"',
    '"Listen 👂 up 👆 you <themed group>"',
    '"HEY 👋 <themed group> 😍"',
    '"WHAT 😳 THE 😳 F*CK 😳 is UP <themed group>" (one emoji between each opening word)',
    '"It\'s <day or event> 📅 you know what that means 😏"',
    '"<themed group> RISE UP ⬆️⬆️"',
]
CLOSER_SHAPES = [
    '"SEND 📩 this to <keycap number> <-est themed group>" then lines "0️⃣ back = ...", "<n> back = ...", "<n> back = ..."',
    '"If you don\'t send this to <keycap number> <themed group> by midnight 🌙 ..." then one line of curse and one of reward',
    '"Get 0️⃣ back, you\'re a ...", "Get <n> back, you\'re ...", "Get <n>+ back, you\'re ..." (one per line)',
    '"PASS 🔁 this on to <keycap number> <themed group> or ..." then 2 lines "0️⃣ back = ...", "<n> back = ..."',
    '"FORWARD ➡️ to <keycap number> <themed group>" then lines starting ❌ for what happens if you don\'t and ✅ if you do',
]
DENSIFY_TARGET_PER_100_WORDS = 58.0  # the r/emojipasta median
EMOJI_PER_SLOT = 1.5  # about half the slots come back as stacks
STACK_UPGRADE_BELOW = 0.4  # below this share of stacked emoji spots, every other single emoji gets a slot too


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


MIN_PUNS_USED = 3


def clean_model_text(text: str) -> str:
    """Grok occasionally emits a broken emoji as U+FFFD. Repair keycaps ("2️�" -> "2️⃣") and drop the rest."""
    text = re.sub(r"([0-9#*])\ufe0f?\ufffd", "\\1\ufe0f\u20e3", text)
    text = text.replace("\ufffd", "")
    return re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)


def puns_used(text: str, puns) -> int:
    """How many of the planned puns ("cirCUMference", or "Blackwell → BlackWELL-HUNG") appear in the text."""
    low = text.lower()
    found = 0
    for pun in puns if isinstance(puns, list) else []:
        word = re.sub(r"\(.*?\)", "", re.split(r"→|->|=|:", str(pun))[-1]).strip(" \"'").lower()
        if word and word in low:
            found += 1
    return found


def _problems(text: str, headline: str, puns=None) -> tuple[list[str], list[str]]:
    """Returns (style problems, content problems). Style problems are tolerated in a last-resort fallback;
    content problems (children mentioned in a sexual post) never are."""
    problems, content = [], []
    density, words = emoji_density(text), len(_words(text))
    if density < MIN_EMOJI_PER_100_WORDS:
        problems.append(f"only {density:.0f} emoji per 100 words (needs 50-60)")
    if tail_density(text) < MIN_EMOJI_PER_100_WORDS:
        problems.append("the ending runs out of emoji")
    if words > MAX_WORDS:
        problems.append(f"{words} words (needs 120-220)")
    if density > MAX_EMOJI_PER_100_WORDS:
        problems.append(f"{density:.0f} emoji per 100 words is spam (needs 50-60)")
    if puns_used(f"{text} {headline}", puns) < MIN_PUNS_USED:
        problems.append(
            f"only {puns_used(f'{text} {headline}', puns)} of your puns made it into the text (needs {MIN_PUNS_USED}+); "
            f"build the jokes from this story's own words and keep a double entendre in every sentence"
        )
    if emoji_variety(text) < MIN_EMOJI_VARIETY:
        problems.append("the same few emoji are repeated over and over")
    # Also check with emoji stripped, since a slot can land inside a phrase ("since 🕰️ they were SEVEN").
    plain = " ".join(EMOJI_PATTERN.sub(" ", f"{text} {headline}").split())
    if SEXUAL_PATTERN.search(text) and MINOR_PATTERN.search(f"{text} {headline} {plain}"):
        minors = " ".join(sorted({m.lower() for m in MINOR_PATTERN.findall(f"{text} {headline} {plain}")}))
        content.append(f"it mentions children/ages under 18 ({minors}) in a sexual post; leave every child out of it")
    return problems, content


def _add_slots(text: str, after_bare_words: int) -> tuple[str, int]:
    """Insert " [[n]]" after punchy words once `after_bare_words` words in a row have had no emoji, and, when the draft
    has too few stacks, glue "[[n]]" onto every other single emoji so it can become a stack.
    Returns (slotted text, slot count)."""
    out, bare, n = [], 0, 0
    upgrade_singles, singles_seen = stack_ratio(text) < STACK_UPGRADE_BELOW, 0
    tokens = re.split(r"(\s+)", text)
    for i, token in enumerate(tokens):
        if not token or token.isspace():
            out.append(token)
            if "\n" in token:
                bare = 0  # chain-letter lines and paragraphs start fresh
            continue
        if EMOJI_PATTERN.search(token):
            bare = 0
            clusters = EMOJI_PATTERN.findall(token)
            next_token = next((t for t in tokens[i + 1:] if t and not t.isspace()), "")
            if (upgrade_singles and token.endswith(clusters[-1]) and len(SINGLE_EMOJI_PATTERN.findall(clusters[-1])) == 1
                    and not EMOJI_PATTERN.match(next_token)):
                singles_seen += 1
                if singles_seen % 2:
                    n += 1
                    token = f"{token}[[{n}]]"
            out.append(token)
            continue
        core = token.rstrip(".,;:!?\"')‼…")
        word = core.lower().strip("\"'(")
        bare += 1
        next_word = next((t for t in tokens[i + 1:] if t and not t.isspace()), "")
        if (bare > after_bare_words and word not in SMALL_WORDS and any(c.isalnum() for c in word)
                and not EMOJI_PATTERN.match(next_word)
                and not (core.istitle() and next_word.istitle())):  # don't split "Stephen Ferrell"
            n += 1
            bare = 0
            token = f"{core} [[{n}]]{token[len(core):]}"
        out.append(token)
    return "".join(out), n


def _densify(client, text: str) -> tuple[str | None, float]:
    """Fill emoji into code-chosen slots of an existing draft. Returns (new text or None, cost in USD)."""
    # Use the widest slot spacing that is projected to reach the target density.
    words, have = len(_words(text)), len(SINGLE_EMOJI_PATTERN.findall(text))
    for after_bare_words in (3, 2, 1):
        slotted, n = _add_slots(text, after_bare_words)
        if (have + n * EMOJI_PER_SLOT) / max(words, 1) * 100 >= DENSIFY_TARGET_PER_100_WORDS:
            break
    if not n:
        return None, 0.0
    result, cost = _chat_json(client, SLOT_FILL_RULES, slotted)
    if not isinstance(result, dict):
        return None, cost

    def fill(m):
        # Keep only emoji from the answer, minus any that already sit on the neighbouring words (the model tends to
        # echo the next word's emoji), and drop the slot entirely if nothing is left. A slot glued to an emoji (no
        # leading space) only adds one, turning that emoji into a 2-stack.
        space, slot = m.groups()
        nearby = set(SINGLE_EMOJI_PATTERN.findall(slotted[max(0, m.start() - 40):m.end() + 40]))
        picked = [e for e in SINGLE_EMOJI_PATTERN.findall(str(result.get(slot, ""))) if e not in nearby | CHILD_EMOJI]
        return space + "".join(picked[:3 if space else 1]) if picked else ""

    new_text = re.sub(r"( ?)\[\[(\d+)\]\]", fill, slotted)
    if emoji_density(new_text) > MAX_EMOJI_PER_100_WORDS:
        return None, cost
    return new_text, cost


def convert_to_emojipasta(article_text, original_title):
    """
    One Grok call writes headline + emojipasta from the article; if it comes back too sparse, a second small call
    (draft only) adds emoji. The full call is retried once (MAX_ATTEMPTS) for bad JSON, length or content problems.
    Returns {"headline", "text"} or None.
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
        f"Now write the emojipasta: 120-200 words, a double entendre in every sentence, about one emoji per two words "
        f"all the way through the chain-letter closer, half of the emoji spots stacked 2-3 deep.\n"
    )
    if random.random() < NICKNAME_CHANCE:
        base_prompt += "Give one person or company in this story a thirsty nickname (like DADDY JACK) and reuse it.\n"
    else:
        base_prompt += "No thirsty nicknames in this one; get the laughs from the metaphor and puns.\n"
    base_prompt += (
        f"Opener shape: {random.choice(OPENER_SHAPES)}\n"
        f"Closer shape: {random.choice(CLOSER_SHAPES)}\nOutput only the JSON described."
    )
    total_cost = 0.0
    best = None
    feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        result, cost = _chat_json(client, STYLE_RULES, base_prompt + feedback)
        total_cost += cost
        if not result or not isinstance(result.get("text"), str) or not result.get("headline"):
            continue
        text, headline = clean_model_text(result["text"].strip()), clean_model_text(result["headline"])
        if emoji_density(text) < DENSIFY_TARGET_PER_100_WORDS or tail_density(text) < MIN_EMOJI_PER_100_WORDS:
            densified, cost = _densify(client, text)
            total_cost += cost
            text = densified or text
        style_problems, content_problems = _problems(text, headline, result.get("puns"))
        problems = content_problems + style_problems
        # Prefer passing attempts, then denser ones; an attempt with a content problem is never used.
        score = (not problems, emoji_density(text))
        if not content_problems and (best is None or score > best[0]):
            best = (score, headline, text)
        if not problems:
            break
        print(f"    attempt {attempt + 1} problems: {'; '.join(problems)}")
        feedback = f"\n\nYour previous attempt failed: {'; '.join(problems)}. Write it again, fixing that. Keep the facts."
    if best is None:
        print(f"  > No usable attempt (bad JSON or content problems). Aborting this article. (cost ${total_cost:.4f})")
        return None
    _, headline, text = best
    print(
        f"  > {emoji_density(text):.0f} emoji/100w (tail {tail_density(text):.0f}), stacks {stack_ratio(text) * 100:.0f}%, "
        f"caps {caps_ratio(text) * 100:.0f}%, {len(_words(text))} words, cost ${total_cost:.4f} ({original_title[:50]})"
    )
    return {"headline": headline, "text": text}


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
