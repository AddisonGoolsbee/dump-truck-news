"""Utility script to rewrite multiple emojipasta headlines with softened ALL CAPS usage."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import system, user

BASE_DIR = Path(__file__).resolve().parent
NEWS_DIR = BASE_DIR.parent / "frontend" / "public" / "news"
ENV_PATH = BASE_DIR / ".env"

# Default target filename can be overridden via CLI args or TARGET_FILE env var.
DEFAULT_FILENAME = "20251123_183012_Trumps_says_Ukraines_leaders_show_zero_gratitude_f.json"


def collect_article_paths(target_filename: str) -> list[Path]:
    """Return every article path whose name sorts <= the target filename."""
    if not NEWS_DIR.is_dir():
        raise FileNotFoundError(f"News directory not found: {NEWS_DIR}")

    target_name = Path(target_filename).name
    candidates = sorted((p for p in NEWS_DIR.glob("*.json") if p.is_file()), key=lambda p: p.name)

    selected = [p for p in candidates if p.name <= target_name]
    if not selected or selected[-1].name != target_name:
        raise FileNotFoundError(f"Target article '{target_name}' not found in {NEWS_DIR}")

    return selected


def rewrite_headline_with_grok(headline: str) -> str:
    """Ask Grok for a tweaked headline with moderate casing adjustments."""
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable is not set")

    client = Client(api_key=api_key, timeout=120)
    chat = client.chat.create(model="grok-4-1-fast-non-reasoning")

    chat.append(
        system(
            """
You are generating a news headline for a satirical emojipasta news site. You are given a headline which is a little too long and a little too all-caps heavy.
return in this format:
{
  "headline": "reworked headline"
}

Guidance:
- Keep all the emoji chaos and energy/vibe of the original headline, but you'll need to make an entirely new headline so that it can be significantly shorter
- Reduce ALL-CAPS usage so that only the punchy words are in ALL CAPS, not the entire headline. Follow these good examples:
- Good example: Admiral Bradley 💦🚤 DROPS Second VENEZUELA Narco-BOAT BOMBS 💣🔥!
- Good example: Afghan 'Animal' 💥🔫 AMBUSHES Guard Bros 😩🏥 Near TRUMP TOWER 🏰‼️
- SHORTEN THE FUCKING HEADLINE IT IS TOO LONG.
- FUCK YOU SHORTEN THE HEADLINE
			"""
        )
    )

    chat.append(user(f"Make a new emojipasta headline with the rules above.\n\nHere is the original headline: {headline}"))

    response = chat.sample()
    result = json.loads(response.content.strip())

    if "headline" not in result:
        raise ValueError("Model response missing 'headline'")

    return result["headline"]


def main():
    load_dotenv(ENV_PATH)

    target_file = os.getenv("TARGET_FILE") or DEFAULT_FILENAME
    if len(sys.argv) > 1:
        target_file = sys.argv[1]

    article_paths = collect_article_paths(target_file)
    print(f"Found {len(article_paths)} files up to '{Path(target_file).name}'.")

    for path in article_paths:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            print(f"Skipping {path.name}: could not read JSON ({exc})")
            continue

        if not isinstance(data, dict):
            print(f"Skipping {path.name}: JSON root is not an object")
            continue

        headline = data.get("headline")
        if not isinstance(headline, str):
            print(f"Skipping {path.name}: missing 'headline'")
            continue

        print(f"Rewriting headline for {path.name}...")
        try:
            new_headline = rewrite_headline_with_grok(headline)
        except Exception as exc:
            print(f"  Failed to rewrite {path.name}: {exc}")
            continue

        data["headline"] = new_headline
        data["headline_rewritten_at"] = datetime.now(timezone.utc).isoformat()

        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        print(f"  Updated headline saved to {path}")


if __name__ == "__main__":
    main()
