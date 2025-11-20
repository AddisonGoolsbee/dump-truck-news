import os
import requests
from datetime import datetime
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import json
from dotenv import load_dotenv

from xai_sdk import Client
from xai_sdk.chat import user, system

# Load environment variables from .env file in the backend directory
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

def fetch_top_news_article():
    """
    Fetch the top news article from BBC RSS feed.
    Returns the article content (title + description + full article text).
    """
    # Fetch BBC RSS feed
    rss_url = "https://feeds.bbci.co.uk/news/rss.xml?edition=us"
    response = requests.get(rss_url)
    response.raise_for_status()
    
    # Parse XML
    root = ET.fromstring(response.content)
    
    # Find the first item (top article)
    item = root.find('.//item')
    if item is None:
        raise ValueError("No articles found in RSS feed")
    
    title = item.find('title').text
    description = item.find('description').text
    link = item.find('link').text
    
    print(f"Fetching article from: {link}")
    
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
    
    return article_text, title

def convert_to_emojipasta(article_text, original_title):
    """
    Use Grok to convert article text to emojipasta format and return structured JSON.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable is not set")

    client = Client(
        api_key=api_key,
        timeout=3600,
    )

    chat = client.chat.create(model="grok-4-1-fast-non-reasoning")
    chat.append(system("""
    You are a text transformation assistant that converts news articles into emojipasta format. You must respond with valid JSON only, no additional text or explanations.

    Example emojipasta style:
    UH-OH⁉️💢 NEW YORK 😱😩🗽 The polls 🗳️✅ have CLOSED 🍆💦🚫 and the people 👨‍👩‍👧‍👦🫂 have SPOKEN 🗣️💋📢‼️ Who’s that 
    👀😳 tapping ✊🔨 that GAVEL 🔨🏛️ of CITY HALL 🤤? It’s ZOHRAN 👑✨ MOMMY 👩‍🍼 DOMMY 💦🤰🏾, the INCOMING 🫃💥 MAYOR 🧑‍⚖️ of 
    your PANTS 👖‼️ He looked 👀 at your RENT 📈🤢 and said "LET'S FREEZE ❄️🧊 THAT" 🥶💦... he looked at daycare 👶🍼 and said 
    "FREE 🆓 UNIVERSAL 🌍 CHILDCARE" 🍼👩‍🍼... and when the billionaires 🧛‍♂️💰 tried to slide into his DMs 📱, he left their 
    PACs 💼🎁 on READ 👁️📵❌! He’s not here to CUT 🪚📉 your taxes 💸😴, he’s here to SPREAD 🫦 YOUR CHEEKS 🍑 and RAISE 📈😍 
    your EXPECTATIONS 🤓📚💫! And we're not just building apartments 🏢, we're giving the whole city a FULL 🍆💦 SUBSIDIZED 
    CLIMAX 💦🎉 where the only thing going UP 📈 is your satisfaction 😩 and the only thing going DOWN 📉 is your rent 🥵! And 
    those buses 🚌? They're not just FREE 🆓, they're giving BACKSHOTS 🏃‍♂️💨🍑 against the schedule ⏱️so frequent 🔄 you'll be 
    seeing stars 🌟💫 on your way to work 💼‼️ So SEND 📤 this to 5️⃣1️⃣% of your local city council 🧍‍♀️🧍🧍‍♂️ to get DOMMED 
    by MOMMY 🤰 If you get 0 back you’re a CUOMO CUCK 👺 If you get 5 back you’re a CITY SLUT 🗽👙 If you get 20 back you’re a 
    CERTIFIED COMMIE CUNT 💅

    You must output valid JSON with exactly these fields:
    {
        "headline": "emojipasta version of the article title",
        "text": "full article content in emojipasta format"
    }
    """))
    chat.append(user(f"Convert this news article to emojipasta format by extracting relevant facts from it and using those facts to come up with an emojipasta article that has lots of emojis and slang for references to people, actions, etc. Use slang for references to popular people and culture. Create an emojipasta headline and full emojipasta text. Article content:\n{article_text}\n\nOutput only valid JSON with 'headline' and 'text' fields."))

    response = chat.sample()

    # Parse the JSON response
    try:
        result = json.loads(response.content.strip())
        return result
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        print(f"Raw response: {response.content}")
        # Fallback: return a basic structure
        return {
            "headline": f"🚨 {original_title} 🚨",
            "text": response.content
        }

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
    print("Fetching top news article...")
    article_text, article_title = fetch_top_news_article()
    print(f"Article fetched: {article_title}\n")

    print("Converting to emojipasta with Grok...")
    emojipasta_data = convert_to_emojipasta(article_text, article_title)
    print("Conversion complete!\n")

    print("Saving to JSON file...")
    filename = save_emojipasta_json(emojipasta_data, article_title)
    print(f"Saved to: {filename}")

    print("\n--- Emojipasta Preview ---")
    print(f"Headline: {emojipasta_data['headline']}")
    print(f"Text preview: {emojipasta_data['text'][:500]}..." if len(emojipasta_data['text']) > 500 else f"Text: {emojipasta_data['text']}")

if __name__ == "__main__":
    main()
