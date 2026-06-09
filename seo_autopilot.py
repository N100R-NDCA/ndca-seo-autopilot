#!/usr/bin/env python3
"""
SEO Autopilot — keyword research, article generation, WordPress publishing.
Runs daily and posts one SEO-optimised article to your WordPress site.
Uses WordPress REST API with Basic Authentication plugin.
"""

import json
import random
import re
import sys
import time
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
    site     = cfg.get("site_description", "an accounting blog for UK founders and small business owners")
    tone     = cfg.get("tone", "clear, direct, and practical — no jargon, no fluff")
    tax_year = cfg.get("tax_year", "2025/26")
    rates    = cfg.get("hmrc_rates", {})

    rates_text = ""
    if rates:
        rates_lines = "\n".join(f"  - {k.replace('_', ' ').title()}: {v}" for k, v in rates.items())
        rates_text = (
            f"\n\nCURRENT HMRC RATES ({tax_year} tax year — use ONLY these figures, do not use older rates):\n"
            f"{rates_lines}\n"
            f"Always state the tax year when quoting figures (e.g. 'In 2025/26...'). "
            f"If a figure is not listed above and you are not certain of the current {tax_year} value, "
            f"say 'check the latest HMRC guidance' rather than quoting a potentially outdated number."
        )

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
        f"{rates_text}"
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
        "- Start with a 2–3 sentence overview paragraph (inside a <p> tag) that summarises what the article covers and who it's for\n"
        "- Immediately after the overview, include a Table of Contents as a <ul> with anchor links to each H2 section — e.g. <li><a href='#section-slug'>Section Title</a></li>\n"
        "- Each H2 must have a matching id attribute that corresponds to its TOC link — e.g. <h2 id='section-slug'>Section Title</h2>\n"
        "- H2 and H3 subheadings throughout\n"
        "- Natural keyword placement (no stuffing)\n"
        "- Practical, actionable advice throughout\n"
        "- A short conclusion without 'In summary' or 'In conclusion'\n"
        "- At least one <ul> or <ol> list\n"
        "- UK spelling and UK tax/accounting context where relevant\n"
        "- No fluff, no padding, no promotional tone"
        f"{links_instruction}"
    )

    print(f"[INFO] Generating article: '{topic}'...")
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        system=system,
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)  # fix invalid JSON escapes
    return json.loads(raw)

# ──────────────────────────────────────────
# Fetch featured image from Pexels
# ──────────────────────────────────────────
def get_featured_image_url(cfg: dict, keyword: str) -> str:
    api_key = cfg.get("pexels_api_key", "")
    if not api_key or api_key == "placeholder":
        return ""
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "orientation": "landscape", "per_page": 5},
            timeout=15,
        )
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            if photos:
                photo = random.choice(photos)
                return photo["src"]["large"]
    except Exception as e:
        print(f"[WARNING] Could not fetch image: {e}")
    return ""


# ──────────────────────────────────────────
# Review and correct article for HMRC accuracy
# ──────────────────────────────────────────
def review_article(cfg: dict, article: dict) -> tuple[dict, bool]:
    """
    Second Claude pass: checks every figure against current HMRC rates,
    corrects wrong tax year references, and returns a corrected article.
    Always publishes — review corrects figures but never blocks publication.
    """
    client   = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    tax_year = cfg.get("tax_year", "2025/26")
    rates    = cfg.get("hmrc_rates", {})

    rates_lines = "\n".join(
        f"  - {k.replace('_', ' ').title()}: {v}" for k, v in rates.items()
    )

    prompt = (
        f"You are a qualified UK chartered accountant reviewing a blog article before publication on an accountancy firm's website.\n\n"
        f"CURRENT HMRC RATES ({tax_year} tax year — these are authoritative):\n{rates_lines}\n\n"
        f"ARTICLE TO REVIEW:\n"
        f"Title: {article['title']}\n\n"
        f"Meta description: {article.get('meta_description', '')}\n\n"
        f"Content:\n{article['html_content']}\n\n"
        "YOUR TASK:\n"
        "1. Check every tax figure, rate, threshold, and allowance against the rates above.\n"
        f"2. Replace any reference to the wrong tax year (e.g. 2024/25) with {tax_year}.\n"
        "3. Correct any figures that don't match the rates provided.\n"
        "4. If a figure or claim cannot be verified from the rates above and could be wrong, "
        "replace it with 'check the latest HMRC guidance for current figures'.\n"
        "5. Do not change the writing style, structure, or any non-tax content.\n\n"
        "Return ONLY valid JSON with these exact keys:\n"
        "  corrected_title          — title with any fixes applied\n"
        "  corrected_meta           — meta description with any fixes applied\n"
        "  corrected_content        — full HTML content with all corrections applied\n"
        "  issues_found             — JSON array of strings describing what was corrected (empty if nothing changed)\n"
        "  verdict                  — 'clean' (no issues), 'corrected' (fixed and ready), or 'needs_review' (uncertain claims remain)\n"
    )

    print("[INFO] Running HMRC accuracy check...")
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)  # fix invalid JSON escapes
    result = json.loads(raw)

    corrected = article.copy()
    corrected["title"]            = result.get("corrected_title",   article["title"])
    corrected["meta_description"] = result.get("corrected_meta",    article.get("meta_description", ""))
    corrected["html_content"]     = result.get("corrected_content", article["html_content"])

    issues = result.get("issues_found", [])
    if issues:
        print(f"[INFO] {len(issues)} correction(s) made:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("[INFO] Article passed accuracy check — no corrections needed.")

    return corrected, True


# ──────────────────────────────────────────
# Publish to WordPress via custom PHP endpoint
# (bypasses Authorization header stripping on Hostinger)
# ──────────────────────────────────────────
def publish_to_wordpress(cfg: dict, article: dict) -> dict:
    wp_url   = cfg["wordpress_url"].rstrip("/")
    username = cfg.get("wordpress_username", "")
    secret   = cfg["wordpress_password"]   # holds the PHP endpoint secret key
    status   = cfg.get("post_status", "draft")
    category = cfg.get("category", "News")

    endpoint = f"{wp_url}/wp-seo-post.php"

    payload = {
        "secret":           secret,
        "username":         username,
        "title":            article["title"],
        "content":          article["html_content"],
        "excerpt":          article.get("meta_description", ""),
        "status":           status,
        "category":         category,
        "focus_keyword":    article.get("focus_keyword", ""),
        "meta_description": article.get("meta_description", ""),
        "image_url":        article.get("image_url", ""),
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    print(f"[INFO] Publishing to WordPress via custom endpoint...")
    resp = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
            break
        except requests.exceptions.Timeout:
            print(f"[WARNING] Attempt {attempt}/3 timed out. Waiting 15s...")
            if attempt < 3:
                time.sleep(15)
            else:
                sys.exit("[ERROR] All 3 attempts timed out. Hostinger may be down — try again later.")

    if resp.status_code == 401:
        print(f"[ERROR] Secret key rejected (401). Update WP_PASSWORD secret to match wp-seo-post.php.")
        sys.exit(1)
    elif resp.status_code == 404:
        print(f"[ERROR] Endpoint not found (404). Check that wp-seo-post.php exists in public_html.")
        sys.exit(1)
    elif resp.status_code not in (200, 201):
        print(f"[ERROR] Failed to create post. Status: {resp.status_code}")
        print(f"[ERROR] Response: {resp.text[:1000]}")
        sys.exit(1)

    post = resp.json()
    return {
        "id":    post.get("id"),
        "url":   post.get("url", f"{wp_url}/?p={post.get('id')}"),
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

    article, is_publishable = review_article(cfg, article)
    print(f"[INFO] Reviewed title: '{article['title']}'")

    image_url = get_featured_image_url(cfg, article.get("focus_keyword", topic))
    if image_url:
        print(f"[INFO] Featured image fetched from Pexels.")
    else:
        print(f"[INFO] No featured image — continuing without one.")
    article["image_url"] = image_url

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
