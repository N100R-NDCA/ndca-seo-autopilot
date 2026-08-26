#!/usr/bin/env python3
"""
SEO Autopilot — keyword research, article generation, WordPress publishing.
Runs daily and posts one SEO-optimised article to your WordPress site.
Uses WordPress REST API with Basic Authentication plugin.
"""

import io
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ──────────────────────────────────────────
# Paths
# ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG = BASE_DIR / "config.json"
TOPICS = BASE_DIR / "topics.json"
LOG = BASE_DIR / "published.json"
LOGOS_DIR = BASE_DIR / "assets" / "logos"
IMAGES_DIR = BASE_DIR / "images"
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")

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
    niche = cfg.get("niche", "accounting and finance for UK small businesses and founders")

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
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    niche = cfg.get("niche", "accounting and finance for UK small businesses and founders")
    site = cfg.get("site_description", "an accounting blog for UK founders and small business owners")
    tone = cfg.get("tone", "clear, direct, and practical — no jargon, no fluff")
    tax_year = cfg.get("tax_year", "2025/26")
    rates = cfg.get("hmrc_rates", {})

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
        "  title — H1 title (under 60 chars, includes primary keyword)\n"
        "  meta_description — meta description (120–155 chars, includes keyword)\n"
        "  focus_keyword — the primary target keyword\n"
        "  category — choose exactly ONE from this list that best fits the article: Payroll, CIS & Construction, Limited Companies, Xero & Software, Sole Traders & Self Assessment, Influencers & Content Creators, Healthcare Professionals, Bookkeeping & Management Accounts\n"
        "  html_content — full article body in HTML (no <html>/<body> wrapper)\n\n"
        "Article requirements:\n"
        "- 1,500–2,500 words\n"
        "- Start with a 2–3 sentence overview paragraph (inside a <p> tag) that summarises what the article covers and who it's for\n"
        "- Immediately after the overview, include a Table of Contents as a <ul> with anchor links to each H2 section — e.g. <li><a href='#section-slug'>Section Title</a></li> — include 'Frequently asked questions' as the last TOC entry linking to #faqs\n"
        "- Each H2 must have a matching id attribute that corresponds to its TOC link — e.g. <h2 id='section-slug'>Section Title</h2>\n"
        "- H2 and H3 subheadings throughout\n"
        "- Natural keyword placement (no stuffing)\n"
        "- Practical, actionable advice throughout\n"
        "- A short conclusion without 'In summary' or 'In conclusion'\n"
        "- At least one <ul> or <ol> list\n"
        "- UK spelling and UK tax/accounting context where relevant\n"
        "- No fluff, no padding, no promotional tone\n"
        "- End the article with an FAQ section: use <h2 id='faqs'>Frequently asked questions</h2> followed by 4–6 questions as <h3> tags, each with a concise answer in a <p> tag. Questions should reflect what someone would actually search for on this topic."
        f"{links_instruction}"
    )

    print(f"[INFO] Generating article: '{topic}'...")
    article_tool = {"name": "submit_article", "description": "Submit the completed, publish-ready SEO blog article.", "input_schema": {"type": "object", "properties": {"title": {"type": "string", "description": "H1 title, under 60 characters, includes the primary keyword."}, "meta_description": {"type": "string", "description": "Meta description, 120-155 characters, includes the keyword."}, "focus_keyword": {"type": "string", "description": "The primary target keyword."}, "category": {"type": "string", "description": "The single category that best fits the article.", "enum": ["Payroll", "CIS & Construction", "Limited Companies", "Xero & Software", "Sole Traders & Self Assessment", "Influencers & Content Creators", "Healthcare Professionals", "Bookkeeping & Management Accounts"]}, "html_content": {"type": "string", "description": "Full article body in HTML, no <html>/<body> wrapper."}}, "required": ["title", "meta_description", "focus_keyword", "category", "html_content"]}}
    msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=8192, messages=[{"role": "user", "content": prompt}], system=system, tools=[article_tool], tool_choice={"type": "tool", "name": "submit_article"})
    tool_use = next((b for b in msg.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return a submit_article tool call.")
    return tool_use.input

# ──────────────────────────────────────────
# Fetch featured image from Pexels
# ──────────────────────────────────────────
def get_featured_image_url(cfg: dict, keyword: str) -> str:
    api_key = cfg.get("pexels_api_key", "")
    if not api_key or api_key == "placeholder":
        return ""
    # This site is a UK chartered accountancy firm. Featured images must never
    # show US-specific tax forms (1040, Schedule C/SE), US currency, USPS
    # labels, or other non-UK content. A bare keyword search on Pexels' mostly
    # US-weighted library returns this kind of image far too often, so we bias
    # the query toward the UK and filter out any candidate whose description
    # matches a known US-content term before falling back to an unfiltered pick.
    blocked_terms = [
        "1040", "irs", "schedule c", "schedule se", "self-employment tax",
        "usps", "us dollar", "dollar bill", "usd", "u.s.", "united states",
        "internal revenue", "w-2", "w2", "federal tax",
    ]
    try:
        query = f"{keyword} UK"
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "orientation": "landscape", "per_page": 15},
            timeout=15,
        )
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            safe_photos = [
                p for p in photos
                if not any(term in (p.get("alt") or "").lower() for term in blocked_terms)
            ]
            candidates = safe_photos or photos
            if candidates:
                photo = random.choice(candidates)
                return photo["src"]["large"]
    except Exception as e:
        print(f"[WARNING] Could not fetch image: {e}")
    return ""

# ──────────────────────────────────────────
# Brand the featured image with the NDCA logo + article title
# ──────────────────────────────────────────
def brand_image(image_bytes: bytes, title: str, cfg: dict) -> bytes:
    """
    Overlay the NDCA logo (auto-picked black or white variant, based on the
    brightness of the photo's top-right corner) and the article title onto
    the featured image. Returns branded JPEG bytes — or the original bytes
    unchanged if anything goes wrong, so a branding failure never blocks
    publishing.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        target_w, target_h = 1200, 630
        img = ImageOps.fit(img, (target_w, target_h), Image.LANCZOS).convert("RGBA")
        draw = ImageDraw.Draw(img, "RGBA")
        W, H = img.size

        # bottom gradient so the title stays legible over any photo
        grad_h = 300
        grad = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(grad)
        for y in range(grad_h):
            alpha = int(210 * (y / grad_h))
            gdraw.line([(0, y), (W, y)], fill=(8, 10, 12, alpha))
        img.paste(grad, (0, H - grad_h), grad)
        draw = ImageDraw.Draw(img, "RGBA")

        # Pick the logo by sampling the top-right corner brightness. The
        # full-colour main logo (black text, teal circle) is used by default
        # on every post — it only switches to the all-white variant when the
        # corner is dark enough that black text wouldn't read cleanly.
        margin = 32
        target_logo_w = 240
        sample_box = (W - target_logo_w - margin, margin, W - margin, margin + 110)
        corner = img.convert("RGB").crop(sample_box)
        brightness = sum(corner.convert("L").getdata()) / (corner.width * corner.height)
        dark_corner = brightness < 140

        preferred = LOGOS_DIR / ("NDCA_Logo_White.png" if dark_corner else "NDCA_Logo_Main.png")
        alternate = LOGOS_DIR / ("NDCA_Logo_Main.png" if dark_corner else "NDCA_Logo_White.png")
        logo_path = preferred if preferred.exists() else (alternate if alternate.exists() else None)

        if logo_path and logo_path.exists():
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.crop(logo.getbbox())
            scale = target_logo_w / logo.width
            logo = logo.resize((target_logo_w, int(logo.height * scale)), Image.LANCZOS)

            # soft scrim tucked into the corner so the logo stays legible
            # even if a future photo's corner turns out the opposite tone
            scrim_w, scrim_h = 340, 170
            scrim = Image.new("RGBA", (scrim_w, scrim_h), (0, 0, 0, 0))
            spx = scrim.load()
            scrim_color = (0, 0, 0) if dark_corner else (255, 255, 255)
            for yy in range(scrim_h):
                for xx in range(scrim_w):
                    d = max(xx / scrim_w, 1 - (yy / scrim_h))
                    spx[xx, yy] = (*scrim_color, int(110 * d))
            scrim = scrim.filter(ImageFilter.GaussianBlur(20))
            img.paste(scrim, (W - scrim_w, 0), scrim)
            draw = ImageDraw.Draw(img, "RGBA")

            img.paste(logo, (W - target_logo_w - margin, margin), logo)
            draw = ImageDraw.Draw(img, "RGBA")
        else:
            print("[WARNING] No logo file found in assets/logos/ — skipping logo overlay.")

        # article title, bottom left
        title_font = ImageFont.truetype(str(FONT_DIR / "LiberationSans-Bold.ttf"), 46)
        tag_font = ImageFont.truetype(str(FONT_DIR / "LiberationSans-Regular.ttf"), 20)

        def wrap_text(text, font, max_width):
            words, lines, current = text.split(), [], ""
            for w in words:
                test = (current + " " + w).strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    current = w
            if current:
                lines.append(current)
            return lines

        lines = wrap_text(title, title_font, W - 100)
        line_height = 56
        start_y = H - 48 - line_height * len(lines)
        for i, line in enumerate(lines):
            draw.text((50, start_y + i * line_height), line, font=title_font, fill=(255, 255, 255, 255))

        # site text, bottom right
        site_tag = cfg.get("wordpress_url", "").replace("https://", "").replace("http://", "").rstrip("/")
        if site_tag:
            bbox = draw.textbbox((0, 0), site_tag, font=tag_font)
            text_w = bbox[2] - bbox[0]
            draw.text((W - margin - text_w, H - 38), site_tag, font=tag_font, fill=(200, 210, 216, 255))

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:
        print(f"[WARNING] Branding failed, using original image unbranded: {e}")
        return image_bytes

# ──────────────────────────────────────────
# Brand the image, commit it to the repo, and return its public URL
# ──────────────────────────────────────────
def brand_and_publish_image(cfg: dict, source_url: str, title: str) -> str:
    """
    Downloads the stock photo, brands it, commits it to images/ in this
    repo, pushes, and returns the public raw.githubusercontent.com URL for
    WordPress to fetch. Falls back to the original unbranded source_url if
    anything in this pipeline fails, so a branding or git issue never blocks
    the day's post from publishing.
    """
    try:
        resp = requests.get(source_url, timeout=20)
        resp.raise_for_status()
        branded_bytes = brand_image(resp.content, title, cfg)

        IMAGES_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        filename = f"{datetime.now().strftime('%Y-%m-%d')}-{slug}.jpg"
        filepath = IMAGES_DIR / filename
        with open(filepath, "wb") as f:
            f.write(branded_bytes)
        print(f"[INFO] Branded image saved: images/{filename}")

        rel_path = f"images/{filename}"
        subprocess.run(["git", "add", rel_path], check=True, cwd=BASE_DIR)
        commit = subprocess.run(
            ["git", "commit", "-m", f"Add branded image for {title}"],
            cwd=BASE_DIR, capture_output=True, text=True,
        )
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
            print(f"[WARNING] git commit issue: {commit.stdout} {commit.stderr}")
        subprocess.run(["git", "push"], check=True, cwd=BASE_DIR)

        repo = os.environ.get("GITHUB_REPOSITORY", "N100R-NDCA/ndca-seo-autopilot")
        raw_url = f"https://raw.githubusercontent.com/{repo}/main/{rel_path}"
        print(f"[INFO] Branded image pushed: {raw_url}")
        return raw_url
    except Exception as e:
        print(f"[WARNING] Could not brand/publish image, falling back to the original: {e}")
        return source_url

# ──────────────────────────────────────────
# Review and correct article for HMRC accuracy
# ──────────────────────────────────────────
def review_article(cfg: dict, article: dict) -> tuple[dict, bool]:
    """
    Second Claude pass: checks every figure against current HMRC rates,
    corrects wrong tax year references, and returns a corrected article.
    Always publishes — review corrects figures but never blocks publication.
    """
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    tax_year = cfg.get("tax_year", "2025/26")
    rates = cfg.get("hmrc_rates", {})

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
        "  corrected_title — title with any fixes applied\n"
        "  corrected_meta — meta description with any fixes applied\n"
        "  corrected_content — full HTML content with all corrections applied\n"
        "  issues_found — JSON array of strings describing what was corrected (empty if nothing changed)\n"
        "  verdict — 'clean' (no issues), 'corrected' (fixed and ready), or 'needs_review' (uncertain claims remain)\n"
    )

    print("[INFO] Running HMRC accuracy check...")
    review_tool = {"name": "submit_review", "description": "Submit the HMRC accuracy review and corrected article.", "input_schema": {"type": "object", "properties": {"corrected_title": {"type": "string", "description": "Title with any fixes applied."}, "corrected_meta": {"type": "string", "description": "Meta description with any fixes applied."}, "corrected_content": {"type": "string", "description": "Full HTML content with all corrections applied."}, "issues_found": {"type": "array", "items": {"type": "string"}, "description": "What was corrected. Empty array if nothing changed."}, "verdict": {"type": "string", "enum": ["clean", "corrected", "needs_review"], "description": "clean = no issues, corrected = fixed and ready, needs_review = uncertain claims remain."}}, "required": ["corrected_title", "corrected_meta", "corrected_content", "issues_found", "verdict"]}}
    msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=8192, messages=[{"role": "user", "content": prompt}], tools=[review_tool], tool_choice={"type": "tool", "name": "submit_review"})
    tool_use = next((b for b in msg.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return a submit_review tool call.")
    result = tool_use.input

    corrected = article.copy()
    corrected["title"] = result.get("corrected_title", article["title"])
    corrected["meta_description"] = result.get("corrected_meta", article.get("meta_description", ""))
    corrected["html_content"] = result.get("corrected_content", article["html_content"])

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
    wp_url = cfg["wordpress_url"].rstrip("/")
    username = cfg.get("wordpress_username", "")
    secret = cfg["wordpress_password"]  # holds the PHP endpoint secret key
    status = cfg.get("post_status", "draft")
    category = cfg.get("category", "Blog")
    topic_category = str(article.get("category", "")).strip()
    if topic_category and topic_category.lower() != category.lower():
        category = category + ", " + topic_category

    endpoint = f"{wp_url}/wp-seo-post.php"

    payload = {
        "secret": secret,
        "username": username,
        "title": article["title"],
        "content": article["html_content"],
        "excerpt": article.get("meta_description", ""),
        "status": status,
        "category": category,
        "focus_keyword": article.get("focus_keyword", ""),
        "meta_description": article.get("meta_description", ""),
        "image_url": article.get("image_url", ""),
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
        "id": post.get("id"),
        "url": post.get("url", f"{wp_url}/?p={post.get('id')}"),
        "title": article["title"],
    }

# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f" SEO Autopilot — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    cfg = load_config()
    log = load_log()

    topic = pick_topic(cfg, log)
    print(f"[INFO] Topic selected: {topic}")

    article = generate_article(cfg, topic)
    print(f"[INFO] Article generated: '{article['title']}'")
    print(f"[INFO] Focus keyword: {article.get('focus_keyword', 'n/a')}")

    article, is_publishable = review_article(cfg, article)
    print(f"[INFO] Reviewed title: '{article['title']}'")

    image_url = get_featured_image_url(cfg, article.get("focus_keyword", topic))
    if image_url:
        print(f"[INFO] Featured image fetched from Pexels.")
        branded_url = brand_and_publish_image(cfg, image_url, article["title"])
        article["image_url"] = branded_url
    else:
        print(f"[INFO] No featured image — continuing without one.")
        article["image_url"] = ""

    result = publish_to_wordpress(cfg, article)
    print(f"\n[SUCCESS] Posted: {result['title']}")
    print(f"[SUCCESS] Post ID: {result.get('id')}")
    print(f"[SUCCESS] URL: {result.get('url')}")

    log.append({
        "topic": topic,
        "title": article["title"],
        "keyword": article.get("focus_keyword", ""),
        "post_id": result.get("id"),
        "url": result.get("url"),
        "published": datetime.now().isoformat(),
    })
    save_log(log)
    print(f"\n[INFO] Log updated ({len(log)} total posts).\n")

if __name__ == "__main__":
    main()
