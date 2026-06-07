#!/usr/bin/env python3
"""
SEO Autopilot — keyword research, article generation, WordPress publishing.
Runs daily and posts one SEO-optimised article to your WordPress site.
Uses WordPress XML-RPC — works with your regular username and password, no plugins needed.
"""

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods.posts import NewPost
from wordpress_xmlrpc.methods.taxonomies import GetTerms

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
        sys.exit("[ERROR] config.json not found. Copy config.example.json → config.json and fill in your details.")
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
        model="claude-opus-4-6",
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
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    niche  = cfg.get("niche", "accounting and finance for UK small businesses and founders")
    site   = cfg.get("site_description", "an accounting blog for UK founders and small business owners")
    tone   = cfg.get("tone", "clear, direct, and practical — no jargon, no fluff")

    system = (
        f"You are an expert SEO content writer specialising in {niche}. "
        f"The blog is {site}. "
        f"Tone: {tone}. "
        "Write for humans first, search engines second. "
        "Never use the banned words: additionally, align with, boasts, bolstered, crucial, delve, emphasizing, "
        "enduring, enhance, fostering, garner, highlight/highlights as a verb, interplay, intricate, key as filler, "
        "landscape in abstract use, meticulous, pivotal, showcase, tapestry, testament, underscore as a verb, "
        "valuable, vibrant, groundbreaking, renowned, diverse array, rich heritage, commitment to. "
        "Never end a sentence with a dangling '-ing' editorial phrase. "
        "Write in short declarative sentences. Use UK English."
    )

    internal_links = cfg.get("internal_links", {})
    links_instruction = ""
    if internal_links:
        links_list = "\n".join(f'  - "{k}" → {v}' for k, v in internal_links.items())
        links_instruction = (
            f"\n- Where relevant, naturally link to these internal service pages using the anchor text shown:\n{links_list}\n"
            "  Only link where it genuinely fits — do not force links."
        )

    prompt = (
        f"Write a complete, publish-ready SEO blog article about: '{topic}'.\n\n"
        "Return ONLY valid JSON with these exact keys:\n"
        "  title          — H1 title (under 60 chars, includes primary keyword)\n"
        "  meta_description — meta description (120–155 chars, includes keyword)\n"
        "  focus_keyword  — the primary target keyword\n"
        "  html_content   — full article body in HTML (no <html>/<body> wrapper)\n\n"
        "Article requirements:\n"
        "- 1,500–2,500 words\n"
        "- H2 and H3 subheadings throughout\n"
        "- Natural keyword placement (no stuffing)\n"
        "- A clear intro that answers the question quickly\n"
        "- Practical, actionable advice throughout\n"
        "- A short conclusion without 'In summary' or 'In conclusion'\n"
        "- At least one <ul> or <ol> list\n"
        "- UK spelling and UK tax/accounting context where relevant\n"
        "- No fluff, no padding, no promotional tone"
        f"{links_instruction}"
    )

    print(f"[INFO] Generating article: '{topic}'...")
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        system=system,
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)

# ──────────────────────────────────────────
# Publish to WordPress via XML-RPC
# ──────────────────────────────────────────
def publish_to_wordpress(cfg: dict, article: dict) -> dict:
    wp_url    = cfg["wordpress_url"].rstrip("/") + "/xmlrpc.php"
    username  = cfg["wordpress_username"]
    password  = cfg["wordpress_password"]
    status    = cfg.get("post_status", "draft")
    category  = cfg.get("category", "News")

    print(f"[INFO] Connecting to WordPress ({wp_url})...")
    client = Client(wp_url, username, password)

    # Find or create category
    terms = client.call(GetTerms("category"))
    cat_id = None
    for term in terms:
        if term.name.lower() == category.lower():
            cat_id = term.id
            break

    post = WordPressPost()
    post.title   = article["title"]
    post.content = article["html_content"]
    post.excerpt = article.get("meta_description", "")
    post.post_status = status

    if cat_id:
        post.terms_names = {"category": [category]}
    else:
        post.terms_names = {"category": [category]}

    # Add custom fields for Yoast / Rank Math
    post.custom_fields = [
        {"key": "_yoast_wpseo_metadesc",   "value": article.get("meta_description", "")},
        {"key": "_yoast_wpseo_focuskw",    "value": article.get("focus_keyword", "")},
        {"key": "rank_math_focus_keyword", "value": article.get("focus_keyword", "")},
        {"key": "rank_math_description",   "value": article.get("meta_description", "")},
    ]

    print(f"[INFO] Publishing as '{status}'...")
    post_id = client.call(NewPost(post))

    return {
        "id":    post_id,
        "url":   f"{cfg['wordpress_url']}/?p={post_id}",
        "title": article["title"],
    }

# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"  SEO Autopilot  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    cfg  = load_config()
    log  = load_log()

    topic   = pick_topic(cfg, log)
    print(f"[INFO] Topic selected: {topic}")

    article = generate_article(cfg, topic)
    print(f"[INFO] Article generated: '{article['title']}'")
    print(f"[INFO] Focus keyword: {article.get('focus_keyword', 'n/a')}")

    result  = publish_to_wordpress(cfg, article)
    print(f"\n[SUCCESS] Posted: {result['title']}")
    print(f"[SUCCESS] Post ID: {result.get('id')}")
    print(f"[SUCCESS] URL: {result.get('url')}")

    log.append({
        "topic":     topic,
        "title":     article["title"],
        "keyword":   article.get("focus_keyword", ""),
        "post_id":   result.get("id"),
        "url":       result.get("url"),
        "published": datetime.now().isoformat(),
    })
    save_log(log)
    print(f"\n[INFO] Log updated ({len(log)} total posts).\n")

if __name__ == "__main__":
    main()
