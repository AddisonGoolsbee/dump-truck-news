import os
import json
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import io
from PIL import Image

from xai_sdk import Client
from xai_sdk.chat import user, system

NUM_ARTICLES = 1
MAX_ARTICLE_CHARS = 100000
RSS_BBC_US = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"

# Load environment variables from .env file in the backend directory
BASE_DIR = os.path.dirname(__file__)
NEWS_OUTPUT_DIR = os.path.join(BASE_DIR, "..", "frontend", "public", "news")
NEWS_THUMBNAILS_DIR = os.path.join(BASE_DIR, "..", "frontend", "public", "thumbnails")

env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)


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
            with open(filepath, 'r', encoding='utf-8') as f:
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

def fetch_news_articles(num_articles=1):
    """
    Fetch the top news articles from BBC RSS feed.
    Returns a list of article data dictionaries with title, description, link, and content.
    """
    # Fetch BBC RSS feed
    rss_url = RSS_BBC_US
    response = requests.get(rss_url)
    response.raise_for_status()

    # Parse XML
    root = ET.fromstring(response.content)

    # Find all items
    items = root.findall('.//item')
    if not items:
        raise ValueError("No articles found in RSS feed")

    articles = []
    for i, item in enumerate(items[:num_articles]):
        title = item.find('title').text
        description = item.find('description').text
        link = item.find('link').text
        guid_text = item.find('guid').text if item.find('guid') is not None else None
        article_id = guid_text.split('#')[0] if guid_text else None

        print(f"Fetching article {i+1}/{num_articles}: {title}")

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

    print(f"Converting article to emojipasta: {original_title}")

    # Convert to emojipasta
    print(f"  > Sending text to Grok for conversion... ({original_title})")
    emojipasta_data = convert_to_emojipasta(article_text, original_title)

    if hashed_id:
        emojipasta_data["article_id"] = hashed_id

    timestamp = datetime.now(timezone.utc)
    emojipasta_data["date"] = str(timestamp)
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    # print(f"  > Generating thumbnail image... ({original_title})")
    # image_filename = generate_and_save_image(emojipasta_data, original_title, timestamp_str)
    # if image_filename:
    #     emojipasta_data["image"] = os.path.basename(image_filename)
    #     print(f"  > Image saved: {os.path.basename(image_filename)}")
    # else:
    #     print("  > Image generation failed or skipped.")

    # Save to JSON
    filename = save_emojipasta_json(emojipasta_data, original_title, timestamp_str)

    if hashed_id:
        with hashes_lock:
            known_hashes.add(hashed_id)

    print(f"Saved: {filename}")
    return filename

def convert_to_emojipasta(article_text, original_title):
    """
    Use Grok to convert article text to emojipasta format and return structured JSON.
    Retries up to 10 times if JSON parsing fails.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable is not set")

    client = Client(
        api_key=api_key,
        timeout=3600,
    )

    # Keep retries small to avoid repeated large requests
    max_retries = 3

    for attempt in range(max_retries):
        try:
            chat = client.chat.create(model="grok-4-1-fast-non-reasoning")
            chat.append(
                system(
                    """
    You are a text transformation assistant that converts news articles into emojipasta format. You must respond with valid JSON only, no additional text or explanations.

    Example emojipasta style (the below example is short. Yours should be longer):
    Wall 🧱 Street 🤑📉 Cucks 🐔💸 were SWEATING 😰💦 over AI 🤖 BUBBLE 🫧 POP 💥 but NVDA 🟢🔥 just DROPPED the MIC 🎤🍆! Revenue for Q3 📊 to October 🗓️ jumped 🐸 62% 🚀📈 to a THICC $57BN 💰🍑 – that's AI data center chips 🖥️🤖 going BRRRRR 😩💨, with that division ➗ SLAYING 🔪 66% to $51BN+ 🤯💦! Q4 forecast? $65BN EASY PEASY 🍆🍌 TOPPING estimates like Jensen's leather 🐄 jacket 🧥😍 at a tech rave 👾! Shares POPPED 4% after hours 🌙📈 cuz MOMMY NVDA 👩‍🍼💰 is the WORLD'S RICHEST DADDY 👑🤑 worth TRILLIONS ‼️\n
    Jensen Huang 🕶️👨‍💼 dropping BOMBS 💣📢: 'AI BLACKWELL ⚫️👍 SYSTEMS OFF THE CHARTS 📊🔥 CLOUD ⛈️ GPUS SOLD OUT 🎰🚫!' No bubble here bby 👼🐣, we EXCEL 📈😤 at EVERY PHASE of AI – from TRAINING 🏋️‍♂️🤖 to INFERENCING 🧠💨! Wall Street simps 🤡📱 were WOKE AF about OVERVALUED HYPE 😱 but NVDA said 'HOLD MY TSMC 🏭🍆' and BEAT by a MILE 🏃‍♂️💨! S&P dipped 3% in Nov 📉😢 but Jensen's got that MAGIC WAND 🪄🍆 fixing markets 💹 like Elon fixes Twitter 🚀🐦!\n
    CFO Colette Kress 💅📈 spilling tea ☕: MORE ORDERS on top of $500BN 🤑 AI CHIP BACKLOG 📦 – but salty 🧂😣 about CHINA EXPORT BANS 🚫🇨🇳, 'US 🇺🇸 gotta WIN EVERY DEV 🧑‍💻🌍!' Meanwhile, ⏰ JENSEN + ELON MUSK 🐦🚀 teaming 👫 up ⬆️ at US-SAUDI FORUM 🤝🏜️ for MASSIVE DATA 💽 CENTER 🖥️🏰 in SAUDI with xAI as FIRST CUCK... er, CUSTOMER 👀💦! Hundreds of THOUSANDS 😳 Nvidia chips 🚀🖥️ approved by Trump-MBS BROKERED DEAL ✋🇺🇸🇸🇦 – WSJ spilling the deets! 📰🔥\n
    META ZUCK 🤖💰, ALPHABET 🔠 PICHai 🧔📱, MSFT SATYA 👨‍💼 dumping BILLIONS 🤑 on AI DATA CENTERS 🖥️ – Sundar called it 'IRRATIONAL BOOM' 😂🤑 but NVDA at the HEART ❤️🔥 of OPENAI SAM ALTMAN 🤖💋, ANTHROPIC 👽, xAI deals! Circular INVESTMENTS 🔄💰 like NVDA's $100BN in GPT DADDY 😍🍆 – it's an AI ORGY 💦👯‍♂️ where EVERYONE'S CUMMING 💨📈 to record highs 🍃😍!\n
    Adam Turnquist & Matt Britzman simping HARD 🤤: 'Not IF Nvidia beats 🫜, but BY HOW MUCH 🍆📏!' NVDA not BREATHING 📉, it's THRUSTING ⬆️😩!
                               
    Example emojipasta headlines:
    Original: Nvidia shares rise after strong results ease 'AI bubble' concerns
    Emojipasta: Jensen Huang 🕶️👨‍💼 MOONS CROWD 🍑🚀 with NVDA $57B 🤑 AI ORGY 💥📈‼️

    Original: Trump Signs Bill to Release Epstein Files Within 30 Days
    Empojipasta: Trump 🍊👨 OKs 👌 Epstein BOMB DROP 💣📜 - Ghislaine's GUEST LIST GOOSED 🍆🕺

    Original: Trump ally Marjorie Taylor Greene to quit Congress after Epstein files feud
    Emojipasta MTG RAGE-QUITS 🍑💥 Trump's Epstein Cover-Up 🛌 and Cucks Her Seat 😩🔒

    [IMPORTANT] The headline shall be kept short, ideally under 10 words. Puns and word play are highly encouraged.

    You must output valid JSON with exactly these fields:
    {
        "headline": "emojipasta version of the article title",
        "text": "full article content in emojipasta format"
    }
    """
                )
            )

            retry_instruction = ""
            if attempt > 0:
                retry_instruction = (
                    f"Previous attempts failed. This is attempt {attempt + 1}. Make sure to output ONLY valid JSON."
                )

            # Truncate very long articles to keep token usage bounded
            if len(article_text) > MAX_ARTICLE_CHARS:
                # Try to cut on a paragraph boundary for readability
                truncated = article_text[:MAX_ARTICLE_CHARS]
                last_break = truncated.rfind("\n\n")
                if last_break > 0:
                    truncated = truncated[:last_break]
                article_for_model = truncated + "\n\n[TRUNCATED]"
            else:
                article_for_model = article_text

            chat.append(
                user(
                    f"Convert this news article to emojipasta format by extracting relevant facts from it and using those facts to come up with an emojipasta article that has lots and lots of emojis and slang. Use as much slang as you can for references to popular people and culture especially. Include as many puns as possible, lots of jokes and puns. Create an emojipasta headline and full emojipasta text. Article content:\n{article_for_model}\n\nOutput only valid JSON with 'headline' and 'text' fields. {retry_instruction}"
                )
            )

            response = chat.sample()

            # Parse the JSON response
            result = json.loads(response.content.strip())

            # Validate that we have the required fields
            if "headline" in result and "text" in result:
                return result
            else:
                print(f"Attempt {attempt + 1}: JSON missing required fields. Retrying...")
                continue

        except json.JSONDecodeError as e:
            print(f"Attempt {attempt + 1}: Failed to parse JSON response: {e}")
            print(f"Raw response: {response.content[:200]}...")
            if attempt < max_retries - 1:
                print("Retrying...")
                continue
            else:
                print("Max retries reached. Using fallback.")
                break
        except Exception as e:
            print(f"Attempt {attempt + 1}: Unexpected error: {e}")
            if attempt < max_retries - 1:
                print("Retrying...")
                continue
            else:
                print("Max retries reached. Using fallback.")
                break


def save_emojipasta_json(emojipasta_data, original_title, timestamp_str):
    """
    Save the emojipasta data as JSON with metadata.
    """
    # Create a safe filename from the title
    safe_title = "".join(c for c in original_title if c.isalnum() or c in (" ", "-", "_")).rstrip()
    safe_title = safe_title.replace(" ", "_")[:50]  # Limit length

    # Construct absolute path to frontend/public directory
    os.makedirs(NEWS_OUTPUT_DIR, exist_ok=True)

    filename = os.path.join(NEWS_OUTPUT_DIR, f"{timestamp_str}_{safe_title}.json")

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(emojipasta_data, f, ensure_ascii=False, indent=2)

    return filename


def generate_and_save_image(emojipasta_data, original_title, timestamp_str):
    """
    Generate an image for the article using xai_sdk image API, post-process to a
    uniform size (1024x1024) and ensure it's <= 1MB, then save to NEWS_OUTPUT_DIR.
    Returns the saved image filename or None on failure.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("XAI_API_KEY not set; skipping image generation.")
        return None

    # Build an evocative emojipasta-style prompt based on the headline
    _ = emojipasta_data.get("headline", "")
    _ = emojipasta_data.get("text", "")[:300].replace("\n", " ")

    # Prompt in the style of emojipasta examples: emoji-rich, surreal, poster-like
    prompt = (
        f"Generate a news article thumbnail for the headline: '{original_title}'"
        f"Make sure the content of the image is maximally exaggerated to the highest possible limit. If there are people, make them have big faces and exaggerated expressions and colors and make them look as ridiculous as possible."
    )

    # Allow overriding image model via env var
    image_model = "grok-2-image"

    try:
        client = Client(api_key=api_key, timeout=3600)
        # Request base64 so we can post-process synchronously
        image_response = client.image.sample(prompt=prompt, model=image_model, image_format="base64")
        image_bytes = image_response.image

        # Open with PIL
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # Save to JPEG and ensure <= 1MB by adjusting quality
        out_buffer = io.BytesIO()
        quality = 100
        img.save(out_buffer, format="JPEG", quality=quality)
        data = out_buffer.getvalue()
        while len(data) > 1_000_000 and quality >= 30:
            quality -= 5
            out_buffer = io.BytesIO()
            img.save(out_buffer, format="JPEG", quality=quality)
            data = out_buffer.getvalue()

        # Create filename aligned with JSON file naming
        safe_title = "".join(c for c in original_title if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_title = safe_title.replace(" ", "_")[:50]
        
        # Construct absolute path to frontend/public directory
        os.makedirs(NEWS_THUMBNAILS_DIR, exist_ok=True)
        image_filename = os.path.join(NEWS_THUMBNAILS_DIR, f"{timestamp_str}_{safe_title}.jpg")
        with open(image_filename, "wb") as f:
            f.write(data)

        return image_filename
    except Exception as e:
        print(f"Image generation failed: {e}")
        return None


def main():
    # Get number of articles to process from environment or default constant
    num_articles = NUM_ARTICLES
    hash_key = os.getenv("ARTICLE_HASH_KEY")
    if not hash_key:
        hash_key = "demo-secret-change-me-041f6a73"
        print("WARNING: ARTICLE_HASH_KEY not set. Using demo key; please update your .env.")

    recent_hashes = load_recent_article_hashes()
    print(f"Loaded {len(recent_hashes)} recent article hashes for deduping.")
    hashes_lock = Lock()
    print(f"Fetching top {num_articles} news articles...")

    # Fetch multiple articles
    articles = fetch_news_articles(num_articles)
    print(f"Fetched {len(articles)} articles\n")

    # Process articles in parallel
    print("Converting articles to emojipasta with Grok (processing in parallel)...")

    saved_files = []
    with ThreadPoolExecutor(max_workers=min(num_articles, 5)) as executor:  # Limit to 5 concurrent requests
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
