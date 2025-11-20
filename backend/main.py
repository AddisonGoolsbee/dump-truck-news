import os
import requests
from datetime import datetime
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import json
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

from xai_sdk import Client
from xai_sdk.chat import user, system

NUM_ARTICLES=10
# Load environment variables from .env file in the backend directory
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

def fetch_news_articles(num_articles=1):
    """
    Fetch the top news articles from BBC RSS feed.
    Returns a list of article data dictionaries with title, description, link, and content.
    """
    # Fetch BBC RSS feed
    rss_url = "https://feeds.bbci.co.uk/news/rss.xml?edition=us"
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

        print(f"Fetching article {i+1}/{num_articles}: {title}")

        try:
            # Fetch the full article page
            article_response = requests.get(link)
            article_response.raise_for_status()

            # Parse the article page to extract content
            soup = BeautifulSoup(article_response.text, 'html.parser')

            # BBC articles use specific tags for content
            article_paragraphs = []

            # Try to find article body paragraphs
            article_body = soup.find('article')
            if article_body:
                paragraphs = article_body.find_all('p')
                article_paragraphs = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]

            # Fallback: try data-component="text-block"
            if not article_paragraphs:
                text_blocks = soup.find_all(attrs={'data-component': 'text-block'})
                article_paragraphs = [block.get_text().strip() for block in text_blocks if block.get_text().strip()]

            # Combine all content
            full_article = '\n\n'.join(article_paragraphs) if article_paragraphs else description

            # Combine title, description, and full content
            article_text = f"""Title: {title}

{description}

{full_article}"""

            articles.append({
                'title': title,
                'description': description,
                'link': link,
                'content': article_text
            })

        except Exception as e:
            print(f"Error fetching article '{title}': {e}")
            # Add basic info even if full content fetch fails
            articles.append({
                'title': title,
                'description': description,
                'link': link,
                'content': f"""Title: {title}

{description}"""
            })

    return articles

def process_single_article(article_data):
    """
    Process a single article: convert to emojipasta and save to JSON.
    Returns the filename of the saved JSON file.
    """
    article_text = article_data['content']
    original_title = article_data['title']

    print(f"Converting article to emojipasta: {original_title}")

    # Convert to emojipasta
    emojipasta_data = convert_to_emojipasta(article_text, original_title)

    # Save to JSON
    filename = save_emojipasta_json(emojipasta_data, original_title)

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

    max_retries = 10

    for attempt in range(max_retries):
        try:
            chat = client.chat.create(model="grok-4-1-fast-non-reasoning")
            chat.append(system("""
    You are a text transformation assistant that converts news articles into emojipasta format. You must respond with valid JSON only, no additional text or explanations.

    Example emojipasta style:
    UH-OH⁉️💢 NEW YORK 😱😩🗽 The polls 🗳️✅ have CLOSED 🍆💦🚫 and the people 👨‍👩‍👧‍👦🫂 have SPOKEN 🗣️💋📢‼️ Who’s that
    👀😳 tapping ✊🔨 that GAVEL 🔨🏛️ of CITY HALL 🤤? It’s ZOHRAN 👑✨ MOMMY 👩‍🍼 DOMMY 💦🤰🏾, the INCOMING 🫃💥 MAYOR 🧑‍⚖️ of
    your PANTS 👖‼️ He looked 👀 at your RENT 📈🤢 and said "LET'S FREEZE ❄️🧊 THAT" 🥶💦... he looked at daycare 👶🍼 and said
    "FREE 🆓 UNIVERSAL 🌍 CHILDCARE" 🍼👩‍🍼... and when the billionaires 🧛‍♂️💰 tried to slide into his DMs 📱, he left their
    PACs 💼🎁 on READ 👁️📵❌!\n\nHe’s not here to CUT 🪚📉 your taxes 💸😴, he’s here to SPREAD 🫦 YOUR CHEEKS 🍑 and RAISE 📈😍
    your EXPECTATIONS 🤓📚💫! And we're not just building apartments 🏢, we're giving the whole city a FULL 🍆💦 SUBSIDIZED
    CLIMAX 💦🎉 where the only thing going UP 📈 is your satisfaction 😩 and the only thing going DOWN 📉 is your rent 🥵! And
    those buses 🚌? They're not just FREE 🆓, they're giving BACKSHOTS 🏃‍♂️💨🍑 against the schedule ⏱️so frequent 🔄 you'll be
    seeing stars 🌟💫 on your way to work 💼‼️\n\nSo SEND 📤 this to 5️⃣1️⃣% of your local city council 🧍‍♀️🧍🧍‍♂️ to get DOMMED
    by MOMMY 🤰\nIf you get 0 back you’re a CUOMO CUCK 👺\nIf you get 5 back you’re a CITY SLUT 🗽👙\nIf you get 20 back you’re a
    CERTIFIED COMMIE CUNT 💅

    You must output valid JSON with exactly these fields:
    {
        "headline": "emojipasta version of the article title",
        "text": "full article content in emojipasta format"
    }
    """))

            retry_instruction = ""
            if attempt > 0:
                retry_instruction = f"Previous attempts failed. This is attempt {attempt + 1}. Make sure to output ONLY valid JSON."

            chat.append(user(f"Convert this news article to emojipasta format by extracting relevant facts from it and using those facts to come up with an emojipasta article that has lots and lots of emojis and slang. Use as much slang as you can for references to popular people and culture especially. Include as many puns as possible, lots of jokes and puns. Create an emojipasta headline and full emojipasta text. Article content:\n{article_text}\n\nOutput only valid JSON with 'headline' and 'text' fields. {retry_instruction}"))

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


def save_emojipasta_json(emojipasta_data, original_title):
    """
    Save the emojipasta data as JSON with metadata.
    """
    # Create a safe filename from the title
    safe_title = "".join(c for c in original_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_title = safe_title.replace(' ', '_')[:50]  # Limit length

    timestamp = datetime.now()
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    # Create the complete JSON object
    json_data = {
        "headline": emojipasta_data["headline"],
        "date": timestamp.isoformat(),
        "text": emojipasta_data["text"]
    }

    # Construct absolute path to frontend/public directory
    frontend_public_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'public', 'news')
    os.makedirs(frontend_public_dir, exist_ok=True)

    filename = os.path.join(frontend_public_dir, f"{timestamp_str}_{safe_title}.json")

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    return filename

def main():
    # Get number of articles to process from environment variable, default to 1
    num_articles = NUM_ARTICLES
    print(f"Fetching top {num_articles} news articles...")

    # Fetch multiple articles
    articles = fetch_news_articles(num_articles)
    print(f"Fetched {len(articles)} articles\n")

    # Process articles in parallel
    print("Converting articles to emojipasta with Grok (processing in parallel)...")

    saved_files = []
    with ThreadPoolExecutor(max_workers=min(num_articles, 5)) as executor:  # Limit to 5 concurrent requests
        # Submit all tasks
        future_to_article = {executor.submit(process_single_article, article): article for article in articles}

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
            with open(saved_files[0], 'r', encoding='utf-8') as f:
                sample_data = json.load(f)
                print(f"Headline: {sample_data['headline']}")
                print(f"Text preview: {sample_data['text'][:500]}..." if len(sample_data['text']) > 500 else f"Text: {sample_data['text']}")
        except Exception as e:
            print(f"Could not load preview: {e}")

if __name__ == "__main__":
    main()
