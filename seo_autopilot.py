#!/usr/bin/env python3
"""
SEO Autopilot — keyword research, article generation, WordPress publishing.
Runs Tuesday/Thursday/Saturday and posts one SEO-optimised article.
Uses a custom PHP endpoint + HMRC accuracy review + Pexels featured image.
"""

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import requests

# ──────────────────────────────────────────
# Paths
# ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG   = BASE_DIR / "config.json"
TOPICS   = BASE_DIR / "topics.json"
LOG      = BASE_DIR / "published.json"

# ──────────────────────────────────────────
# Load config
# ──────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit("[ERROR] config.json not found.")
    with open(CONFIG) as f:
        return json.load(f)

# ──────────────────────────────────────────
# Load / save published log
# ──────────────────────────────────────────
def load_log() -> list[dict]:
    if LOG.exists():
        with open(LOG) as f:
            return json.load(f)
    return []

def save_log(entries: list[dict]):
    with open(LOG, "w") as f:
        json.dump(entries, f, indent=2)

# ──────────────────────────────────────────
# Pick next topic
# ──────────────────────────────────────────
def pick_topic(cfg: dict, log: list[dict]) -> str:
    used_titles = {e["topic"].lower() for e in log}

    if TOPICS.exists():
        with open(TOPICS) as f:
            bank: list[str] = json.load(f)
        unused = [t for t in bank if t.lower() not in used_titles]
        if unused:
            return random.choice(unused)

    print("[INFO] Topic bank empty. Generating new topics via Claude...")
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    niche  = cfg.get("niche", "accounting and finance for UK small businesses and founders")

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"Generate 20 specific, search-intent SEO article topics for a blog about: {niche}.\n"
                "Each topic should target a keyword a small business owner or founder would search on Google.\n"
                "Focus on practical, problem-solving topics.\n"
                "Return ONLY a JSON array of strings — no explanation, no markdown fences."
            )
        }]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    new_topics: list[str] = json.loads(raw)

    existing: list[str] = []
    if TOPICS.exists():
        with open(TOPICS) as f:
            existing = json.load(f)
    merged = list(dict.fromkeys(existing + new_topics))
    with open(TOPICS, "w") as f:
        json.dump(merged, f, indent=2)
    print(f"[INFO] Added {len(new_topics)} topics to bank.")

    unused = [t for t in merged if t.lower() not in used_titles]
    if not unused:
        sys.exit("[ERROR] All topics already published. Add more to topics.json.")
    return random.choice(unused)

# ──────────────────────────────────────────
# Generate article
# ──────────────────────────────────────────
def generate_article(cfg: dict, topic: str) -> dict:
    client   = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    niche    = cfg.get("niche", "accounting and finance for UK small businesses and founders")
    site     = cfg.get("site_
