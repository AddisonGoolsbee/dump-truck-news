import io
import os
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
MAX_ARTICLE_CHARS = 100000
MAX_IMAGE_GEN_ATTEMPTS = 3

# Roughly matches real r/emojipasta density; below this we ask Grok to try again denser.
EMOJI_PATTERN = re.compile(
    r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF☀-➿⬀-⯿️‍⃣]+"
)
MIN_EMOJI_PER_100_CHARS = 8.0


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

        try:
            # Fetch the full article page
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

            # Combine all content
            full_article = "\n\n".join(article_paragraphs) if article_paragraphs else description

            # Combine title, description, and full content
            article_text = f"""Title: {title}

{description}

{full_article}"""

            articles.append(
                {
                    "title": title,
                    "description": description,
                    "link": link,
                    "content": article_text,
                    "article_id": article_id,
                    "section": section_name,
                }
            )

        except Exception as e:
            print(f"Error fetching article '{title}': {e}")
            # Add basic info even if full content fetch fails
            articles.append(
                {
                    "title": title,
                    "description": description,
                    "link": link,
                    "content": f"""Title: {title}

{description}""",
                    "article_id": article_id,
                    "section": section_name,
                }
            )

    return articles


def process_single_article(article_data, hash_key, known_hashes, hashes_lock):
    """
    Process a single article: convert to emojipasta and save to JSON.
    Returns the filename of the saved JSON file or None if skipped.
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
        return None

    if hashed_id:
        emojipasta_data["article_id"] = hashed_id

    emojipasta_data["section"] = article_data.get("section")

    timestamp = datetime.now(timezone.utc)
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


MODEL = "grok-4.20-reasoning-latest"

# Shared style bible used for every paragraph-generation call. Generating each paragraph as its
# own fresh call (instead of one long generation) is what actually fixes density tapering — a
# single long generation reliably starts dense and fades by the final paragraph no matter how
# hard the prompt insists otherwise, but every paragraph generated fresh against these examples
# opens just as dense as paragraph 1 used to.
PARAGRAPH_STYLE_RULES = """
You are an r/emojipasta poster. You write ONE paragraph at a time of an unhinged "emojipasta" news rewrite: internet copypasta that reads like it was typed by someone way too invested, at 2am, mid rant. Respond with valid JSON only, no additional text.

Below are two FULL, ideal-quality example paragraphs on the same kind of dry political/trade subject matter you'll be working with. Study them closely — they are the bar for density, emoji clustering, caps ratio, punctuation, and innuendo. Every paragraph you write must match this bar, since each paragraph is judged fresh — there is no "easing in," open at full density immediately.

EXAMPLE A:
"SENATE 🏛️ FINALLY 🏁 busts 💦 a NUT 🥜 on that 2 TRILLION 💰 dollar 💵 infraSTUDcture 🍆 bill 📜 after a MARATHON 🏃‍♂️ 15-hour 🕐 session that left 😵‍💫 everyone 🫠 DRIPPING 💧 with EXHAUSTION 🥵!! Majority 👑 Leader 👔 Dale 🍑 Whitfield 🔥, affectionately 💅 known 🏷️ as DILF 🐺 Dale to the interns 👀, STROKED 👐 every senator's 🧑‍⚖️ ego 🥴 one by one 🔂 until they FOLDED 🙇‍♂️ like a cheap 💸 lawn chair 🪑, finally SEALING 💍 the deal 🤝 at 3am 🌙 with a 62-38 vote 🗳️✅!! "We got RAILED 💦 by the process ⚙️," admitted 🎙️ Senator Beth Carrow 😵‍💫, "but honestly 🤭? Kinda into it 😳." The bill INCLUDES 📋 400 billion 💵 for roads 🛣️, 200 billion for BRIDGES 🌉 (which, let's be honest 👀, is a metaphor 🤔 for how BADLY 🩹 both parties 🎭 needed to CONNECT 🔗 again 🔁), and a controversial 😬 rider 📎 that has fiscal hawks 🦅 SCREAMING 😱 bloody 🩸 murder 🔪 into their pillows 🛏️. Republicans 🐘 called it a "GIRTHY 👀 overreach," Democrats 🐴 called it "long OVERDUE 😩," and one anonymous 🤐 staffer 🧑‍💼 called it "the horniest 🌶️ thing I've seen 👁️ on that floor 🪵 since the legiSLAYtion 📜 of '09." Whitfield 🍑 celebrated 🎉 by popping CHAMPAGNE 🍾 in the cloakroom 🚪 and telling reporters 🎙️, breathlessly 😮‍💨, "we came 😳, we saw 👀, we approPORNiated 💰‼️" No cap 🧢, this session 🎬 was a full CANON EVENT 🔥 fr fr 💯, and the only thing DOUBLE-STACKED 💦💦 tonight was the paperwork 📑."

EXAMPLE B:
"TRADE 💼 negotiators 🤝 from three countries 🌍 FINALLY sealed 🔒 a deal 🤝 after 11 STRAIGHT hours 🕐 of back-and-forth 🔄 that left everyone drained 🪫 feeling THOROUGHLY negotiated 🥴. Chief 👑 negotiator 🧑‍💼 Renata Vance 🍑 — known 🏷️ around the ministry 🏛️ as "the CLOSER 🔒" for her ability 💪 to make ANYONE fold 🙇‍♀️ — reportedly kept delegates 🕴️ in the room 🚪 until 4am 🌙, PUMPING 💦 out CONcession after CUMcession 💦 like it was nothing personal 🤷‍♀️, just BUSINESS 💼. "She had us on our KNEES 🙇‍♂️ begging 🥺 for a BREAK 😭," admitted one exhausted 😵 delegate, "and honestly 🤭? We kind of liked it 😳." The final agreement SLASHES 🔪 tariffs 📉 by 15%, opens up DAIRY 🥛 markets 🛒 (a phrase 📝 that will never sound the same again 💦), and includes a side deal 🤫 SO steamy 🌶️ that both sides 👀 had to sign NDAs 🤐. Critics 🗣️ on both sides called it a "BACKDOOR 🚪 giveaway 💰," supporters 👏 called it "long 📏, HARD 🪨, and worth the wait 🍾," and Vance 🍑 herself just smirked 😏 and said 🗣️ "a good deal 🤝 is like a good time 💦 — you don't rush 🏃‍♀️ it, you just let it BUILD 📈." This whole THING 🔥 is giving 😍 unhinged trade-summit-turned-honeymoon-suite 🍯 energy 💫, and the only DOUBLE 👀 anyone got was the DOUBLE-CROSSED 🔪🔪 rider clause buried on page 40 📄."

Break down what makes these work, so you replicate it precisely:
1. EMOJI RHYTHM AND DENSITY — measured, not vibes: count it — these examples average roughly one emoji every 1-3 words. This is genuinely more emoji than feels natural to write — push past the instinct to stop. But do NOT turn this into a rigid metronome of exactly one emoji every single word — look at the gap pattern in the examples: sometimes two emoji land back-to-back words, sometimes there's a 3-5 word stretch with none before the next hits. That irregular rhythm is what makes it read as unhinged enthusiasm rather than a script. If every single gap in your paragraph is the same length, that's a mechanical failure just like the "one big pile of emoji" and "always pairs" failures — vary it.
1b. SINGLES ARE THE DEFAULT — THIS IS CRITICAL: count the examples above — the overwhelming majority of attachment points (roughly 85-90%) are exactly ONE emoji. A 2-emoji stack appears at most ONCE per paragraph, reserved for the single biggest punchline (and even then it's often two DIFFERENT emoji making one joke, like 🔪🔪 for "double-crossed," not just any two emoji glued together as a habit). If you notice yourself pairing 2 emoji together at most attachment points, STOP — that is a mechanical failure that reads as repetitive and lazy, exactly the opposite of what we want. Default to one emoji, one word/phrase, over and over; save a double for one real moment, not a running habit.
1c. NO GENERIC REACTION-FACE FILLER — this is what makes dense emoji read as slop instead of clever: reaching for the same handful of hype/reaction faces (😤 😩 🥵 😳 🔥 💯 🙏 😭 💀 🤯 ✨ 😏) as your default choice for every attachment point. Look at the examples again — the emoji are overwhelmingly CONCRETE and LITERAL: a wooden log 🪵 for "logged off," a magnifying glass 🔍 for "searching," a foot 🦶 for "100 feet," a nut 🥜 for "NUT," a lawn chair 🪑 for the actual chair mentioned, a slice of paper 📑 for "paperwork." For every word you're about to tag, ask "what OBJECT, ANIMAL, FOOD, TOOL, or BODY PART literally relates to this word or sounds like part of it" before defaulting to a generic emotion face. Reaction faces should be the minority of your emoji, used only where a specific reaction genuinely lands better than a concrete/literal choice — not the default.
2. Long direct quotes are NOT an emoji-free zone. If you quote someone at length, either drop emoji right before/after the quoted chunk, or paraphrase the quote shorter and weave emoji through the paraphrase.
3. PUNCTUATION: full normal punctuation — commas, periods, quotation marks around quotes, "!!", question marks. Emoji are inserted into grammatical sentences, never replacing punctuation.
4. CAPS: roughly a third to half of words in caps, but clearly not all — small words (a, the, and, of, to, that, it, etc.) stay lowercase, so the caps still pop as emphasis. Adding more emoji is NOT an excuse to also capitalize more words — these are independent axes. A word can get an emoji while staying lowercase; do not cap something just because you're attaching an emoji to it. This paragraph is checked in isolation — a dramatic-feeling beat is NOT license to push this one paragraph toward all-caps; the 30-45% target applies to every single paragraph individually, not as a piece-wide average.
4b. EMOJI VARIETY — do not lean on the same 2-3 "safe" emoji (😤 😩 🥵 etc.) over and over as filler to hit the density target. Repeating one emoji more than ~3 times in a single paragraph is a sign you're padding rather than choosing — pick the specific emoji that fits each specific word/joke instead, drawing from a wide range (reactions, objects, animals, food, weather, activities), not just your go-to hype faces.
5. INNUENDO IS MANDATORY IN THIS PARAGRAPH TOO, not just somewhere in the piece — force at least one of these techniques into THIS paragraph specifically:
   - word-mangling — this is a SWAP, not an insertion: replace ONE syllable of a real word with a phonetically similar filthy/slang syllable, keeping the rest of the word intact so the whole thing is still ONE pronounceable word, roughly the same length as the original, and instantly recognizable. GOOD: "concessions" -> "CUMcessions" (swap "con" for "CUM", same syllable count, reads instantly). BAD, DO NOT DO THIS: "concessions" -> "conCUMcessions" (inserting extra letters is clunky), "negotiators" -> "NEGOTHRUSTiators" (inserting a whole extra syllable breaks pronounceable flow), "finalize" -> "FINALI SEXY" (this is two separate words glued with a space — not a real word, incoherent, never do this). If you can't find a clean one-syllable SWAP for a word in this paragraph, skip it and use a different technique instead.
   - reframing the mundane action in this beat as a sexual encounter: negotiating = foreplay/edging, a deal closing = the climax, a long session = getting railed/stroked/pumped, a compromise = getting on your knees, going long = worth the wait.
   - use the running nickname (given below) for its target if they appear in this beat, ideally with a thirsty aside.
   - a suggestive quote or aside from a fictionalized bystander reacting to how horny the process felt.

OTHER STYLE RULES:
- Internet/meme slang: bro, bruh, no cap, fr fr, ratio'd, W/L, rent free, delulu, sigma, npc, cooked, mid, built different, down bad, canon event, main character energy, unc, etc. Use naturally, don't force every one in.
- Stay factually grounded: every claim must trace back to the facts given below. Do not invent facts, quotes, or numbers — the innuendo is in the VOICE and WORD CHOICE, not fabricated plot details.
- Output ONE paragraph only, roughly 80-130 words, as a single block of text (no internal line breaks).

You must output valid JSON with exactly these fields:
{
    "paragraph": "the single emojipasta paragraph, ~80-130 words, dense per the rules above"
}
"""


def _chat_json(client, system_prompt, user_prompt, retry_note=""):
    """One JSON-mode chat call with a couple of retries on parse failure. Returns the parsed dict or None."""
    for attempt in range(3):
        try:
            chat = client.chat.create(model=MODEL)
            chat.append(system(system_prompt))
            note = retry_note if attempt == 0 else f"{retry_note} Previous attempt was not valid JSON, attempt {attempt + 1}."
            chat.append(user(f"{user_prompt} {note}".strip()))
            response = chat.sample()
            return json.loads(response.content.strip())
        except json.JSONDecodeError as e:
            print(f"    JSON parse failed (attempt {attempt + 1}): {e}")
        except Exception as e:
            print(f"    Unexpected error (attempt {attempt + 1}): {e}")
    return None


def plan_emojipasta(article_text, original_title, client):
    """One call: produce the headline, a running horny nickname, and 5-6 fact 'beats' (one per paragraph)."""
    if len(article_text) > MAX_ARTICLE_CHARS:
        truncated = article_text[:MAX_ARTICLE_CHARS]
        last_break = truncated.rfind("\n\n")
        article_for_model = (truncated[:last_break] if last_break > 0 else truncated) + "\n\n[TRUNCATED]"
    else:
        article_for_model = article_text

    system_prompt = """
You are prepping a real news article to be rewritten paragraph-by-paragraph as unhinged r/emojipasta comedy. You must respond with valid JSON only.

Do three things:
1. Write a short, punchy emojipasta-style headline for the article (under 10 words). Use normal English capitalization with some ALL CAPS bursts for emphasis (not the whole thing), and 2-4 well-placed emoji (not one pile) — this is the reader's very first impression, make it hit.
2. Pick ONE person or entity named in the article who will get a running horny/thirsty nickname used throughout the piece (e.g. "Wab Kinew" -> "Wab Daddy", "Renata Vance" -> "the Closer"). Keep it a clean pun or thirst-trap title, not explicit.
3. Split the article's actual facts into 5-6 beats, in the article's narrative order, each beat a 2-4 sentence PLAIN ENGLISH summary of the distinct facts/quotes/numbers that one paragraph should cover. Beats should not overlap in content. Keep numbers, names, and quotes accurate to the source.

Output JSON exactly as:
{
    "headline": "...",
    "nickname_target": "the real name of the person getting the nickname",
    "nickname": "the horny nickname",
    "beats": ["beat 1 text", "beat 2 text", "... 5-6 total"]
}
"""
    user_prompt = f"Article title: {original_title}\n\nArticle content:\n{article_for_model}\n\nOutput only the JSON described."
    return _chat_json(client, system_prompt, user_prompt)


def generate_paragraph(beat, headline, nickname_target, nickname, client):
    """Generate one dense emojipasta paragraph for a single beat, retrying toward a density floor
    while independently enforcing a caps ceiling (each paragraph is checked on its own — averaging
    across paragraphs let one paragraph go almost fully caps while others stayed fine)."""
    best_text, best_score = None, -1.0
    feedback = ""

    for attempt in range(3):
        user_prompt = (
            f"The overall piece's headline is: {headline}\n"
            f"Running nickname for this piece: '{nickname}' for {nickname_target} — use it if {nickname_target} "
            f"appears in this beat.\n"
            f"Write ONE paragraph covering exactly these facts (do not add facts not listed here):\n{beat}\n"
            f"Average roughly one emoji every 1-3 words, and VARY the gap unpredictably — some emoji back to "
            f"back or 1 word apart, others 3-5 words apart, don't make every single gap the same length or it "
            f"reads as a robotic metronome. 85-90% of attachments must be a SINGLE emoji, not a pair — at most "
            f"one 2-emoji moment in the whole paragraph, for the single biggest punchline only. Caps stay under "
            f"50% of words (ideally 30-45%) — this paragraph is checked on its own, not averaged with others, "
            f"so don't let this one specific paragraph run hot on caps even if the topic feels dramatic.\n"
            f"{feedback}"
        )
        result = _chat_json(client, PARAGRAPH_STYLE_RULES, user_prompt)
        if not result or "paragraph" not in result:
            continue

        text = result["paragraph"]
        density = emoji_density(text)
        caps = caps_ratio(text)
        slop = slop_ratio(text)
        caps_ok = caps <= MAX_CAPS_RATIO
        slop_ok = slop <= MAX_SLOP_RATIO
        # Heavily discount candidates that blow past the caps/slop ceilings when picking the fallback-best.
        score = density if (caps_ok and slop_ok) else density * 0.3
        if score > best_score:
            best_text, best_score = text, score

        if density >= MIN_EMOJI_PER_100_CHARS and caps_ok and slop_ok:
            return text

        if attempt < 2:
            notes = []
            if density < MIN_EMOJI_PER_100_CHARS:
                notes.append(
                    f"Your last attempt averaged {density:.1f} emoji per 100 characters — still too sparse, "
                    f"needs at least {MIN_EMOJI_PER_100_CHARS:.0f}. Add more SINGLE emoji attachments in the "
                    f"gaps between words — do NOT fix this by pairing up 2 emoji at existing attachment points "
                    f"(that recreates the all-clustered problem), and do NOT fix it by making every gap exactly "
                    f"1 word (that creates a robotic metronome) — vary the gap length."
                )
            if not caps_ok:
                notes.append(
                    f"Your last attempt was {caps * 100:.0f}% ALL CAPS words — way too shouty, over the 50% "
                    f"ceiling. Dial it back to 30-45%: most content words should be normal case, with only a "
                    f"genuine minority capitalized for emphasis."
                )
            if not slop_ok:
                notes.append(
                    f"Your last attempt leaned too hard on generic reaction-face emoji ({', '.join(sorted(SLOP_EMOJI))}) "
                    f"— {slop * 100:.0f}% of your emoji were from that small set, which reads as lazy filler no "
                    f"matter how dense it is. Replace most of those with concrete, literal, or pun emoji tied to "
                    f"the SPECIFIC word next to them (objects, animals, food, tools, body parts, weather) instead "
                    f"of a recycled hype-face."
                )
            feedback = " ".join(notes)

    return best_text


def convert_to_emojipasta(article_text, original_title):
    """
    Use Grok to convert article text to emojipasta format: one call to plan the headline/nickname/beats,
    then one independent, fresh-context call per paragraph (each with its own density retry) so density
    doesn't taper off over the course of a single long generation. Returns {"headline", "text"}.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable is not set")

    client = Client(api_key=api_key, timeout=3600)

    plan = plan_emojipasta(article_text, original_title, client)
    if not plan or not all(k in plan for k in ("headline", "beats")):
        print("  > Failed to plan emojipasta (headline/beats). Aborting this article.")
        return None

    headline = plan["headline"]
    nickname_target = plan.get("nickname_target", "")
    nickname = plan.get("nickname", "")
    beats = plan["beats"]
    print(f"  > Planned {len(beats)} paragraphs, running nickname: '{nickname}' for {nickname_target}")

    paragraphs = [None] * len(beats)
    with ThreadPoolExecutor(max_workers=min(len(beats), 3) or 1) as executor:
        future_to_index = {
            executor.submit(generate_paragraph, beat, headline, nickname_target, nickname, client): i
            for i, beat in enumerate(beats)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                paragraphs[i] = future.result()
            except Exception as e:
                print(f"    Paragraph {i + 1} generation failed: {e}")

    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        print("  > All paragraph generations failed. Aborting this article.")
        return None

    text = "\n\n".join(paragraphs)
    print(f"  > Final density: {emoji_density(text):.2f} emoji/100 chars across {len(paragraphs)} paragraphs.")
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
                saved_files.append(filename)
            except Exception as exc:
                print(f"Article '{article['title']}' generated an exception: {exc}")

    print(f"\nConversion complete! Processed {len(saved_files)} articles.")
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


if __name__ == "__main__":
    main()
