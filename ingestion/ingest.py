#!/usr/bin/env python3
"""
Brain Ingestion Script
======================
Fetches new content for each brain defined in brains.yaml and appends
processed chunks to the corresponding knowledge .md file.

Two sweeps per run:
  - Forward sweep:  picks up content newer than last known item
  - Backfill sweep: works backward through older content (5 items per run)

Usage:
  python ingest.py                  # run all brains
  python ingest.py --brain nate_jones  # run one brain
  python ingest.py --dry-run        # show what would be fetched, don't write
"""

import os
import sys
import json
import time
import hashlib
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
import requests
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from bs4 import BeautifulSoup
import feedparser

sys.path.insert(0, str(Path(__file__).parent.parent))

from common.openrouter import openrouter_chat

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent.parent
BRAINS_DIR    = ROOT / "brains"
STATE_FILE    = ROOT / "ingestion" / "state.json"
CONFIG_FILE   = ROOT / "brains.yaml"
CHUNK_WORDS   = 500
CHUNK_OVERLAP = 50
BACKFILL_BATCH = 5          # items to backfill per run
FORWARD_LIMIT  = 10         # max new items per forward sweep

YT_API_KEY    = os.environ.get("YOUTUBE_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-flash-1.5")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")   # e.g. "yourusername/brain-system"

# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def get_brain_state(state: dict, brain_slug: str) -> dict:
    return state.setdefault(brain_slug, {
        "youtube": {},    # video_id -> True (already ingested)
        "substack": {},   # post_url -> True
        "articles": {},   # url -> True
        "backfill_cursor": {},  # source_key -> oldest_page_token or offset
        "last_run": None,
    })

# ── YouTube helpers ───────────────────────────────────────────────────────────

def resolve_yt_handle(handle: str) -> Optional[str]:
    """Resolve @handle to channel_id using YouTube Data API v3."""
    if not YT_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY not set")
    handle_clean = handle.lstrip("@")
    url = "https://www.googleapis.com/youtube/v3/channels"
    resp = requests.get(url, params={
        "part": "id",
        "forHandle": handle_clean,
        "key": YT_API_KEY,
    }, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        raise ValueError(f"Could not resolve YouTube handle: {handle}")
    return items[0]["id"]

def get_channel_videos(channel_id: str, max_results: int = 50, page_token: str = None) -> tuple[list, Optional[str]]:
    """Return (list of video dicts, next_page_token)."""
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "id,snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "key": YT_API_KEY,
    }
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    videos = [
        {
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "published": item["snippet"]["publishedAt"],
            "channel": item["snippet"]["channelTitle"],
        }
        for item in data.get("items", [])
        if item["id"].get("videoId")
    ]
    return videos, data.get("nextPageToken")

def get_transcript(video_id: str) -> Optional[str]:
    """Fetch transcript text for a video. Returns None if unavailable."""
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"])
        return " ".join(t["text"] for t in transcript)
    except (TranscriptsDisabled, NoTranscriptFound):
        log.warning(f"No transcript for video {video_id}")
        return None
    except Exception as e:
        log.error(f"Transcript error for {video_id}: {e}")
        return None

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch_soup(url: str, timeout: int = 15) -> BeautifulSoup:
    """GET a URL with a browser User-Agent and parse it with BeautifulSoup."""
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    return BeautifulSoup(resp.text, "html.parser")

# ── Substack helpers ──────────────────────────────────────────────────────────

def get_substack_posts(base_url: str, already_seen: dict) -> list:
    """Fetch new posts from a Substack via its RSS feed."""
    rss_url = base_url.rstrip("/") + "/feed"
    feed = feedparser.parse(rss_url)
    posts = []
    for entry in feed.entries:
        url = entry.get("link", "")
        if url in already_seen:
            continue
        # fetch full content
        try:
            soup = fetch_soup(url)
            # Substack puts article content in .available-content or article tag
            content_el = soup.find("div", class_="available-content") or soup.find("article")
            text = content_el.get_text(separator=" ", strip=True) if content_el else ""
        except Exception as e:
            log.warning(f"Could not fetch substack post {url}: {e}")
            text = entry.get("summary", "")
        posts.append({
            "url": url,
            "title": entry.get("title", "Untitled"),
            "published": entry.get("published", ""),
            "text": text,
        })
    return posts

# ── Web article helpers ───────────────────────────────────────────────────────

def scrape_article(url: str) -> Optional[str]:
    """Simple article scraper - extracts main text content."""
    try:
        soup = fetch_soup(url)
        # Remove nav, header, footer, scripts
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()
        # Try article tag first, then main, then body
        content = soup.find("article") or soup.find("main") or soup.find("body")
        return content.get_text(separator=" ", strip=True) if content else None
    except Exception as e:
        log.error(f"Could not scrape {url}: {e}")
        return None

# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping word-count chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = words[i: i + chunk_words]
        chunks.append(" ".join(chunk))
        i += chunk_words - overlap
    return [c for c in chunks if len(c.split()) > 50]  # drop tiny tail chunks

# ── LLM summarisation ─────────────────────────────────────────────────────────

def summarise_chunk(chunk: str, brain_config: dict) -> dict:
    """Send a chunk to OpenRouter and get summary + tags back."""
    if not OPENROUTER_KEY:
        # Fallback: use first 300 chars as summary, no tags
        return {"summary": chunk[:300] + "...", "tags": []}

    prompt = f"""You are processing content for a knowledge base about {brain_config['display_name']}.

Given this text chunk, provide:
1. A concise summary (2-4 sentences) capturing the key claims and insights
2. Up to 5 specific topic tags (from the expertise areas: {', '.join(brain_config['expertise_tags'])})

Respond in JSON only:
{{"summary": "...", "tags": ["tag1", "tag2"]}}

Text chunk:
{chunk[:3000]}"""

    try:
        content = openrouter_chat(
            OPENROUTER_KEY,
            OPENROUTER_MODEL,
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.2,
            timeout=30,
        )
        # Strip markdown fences if present
        content = content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(content)
    except Exception as e:
        log.warning(f"LLM summarisation failed: {e}")
        return {"summary": chunk[:300] + "...", "tags": []}

# ── Knowledge file writer ─────────────────────────────────────────────────────

def append_to_brain(brain_slug: str, brain_config: dict, entry: dict, dry_run: bool = False):
    """Append a processed entry to the brain's .md knowledge file."""
    BRAINS_DIR.mkdir(parents=True, exist_ok=True)
    brain_file = BRAINS_DIR / f"{brain_slug}.md"

    # Create file with header if it doesn't exist
    if not brain_file.exists() and not dry_run:
        header = f"""# {brain_config['display_name']} — Knowledge Brain

**Expertise:** {', '.join(brain_config['expertise_tags'])}
**Style:** {brain_config['style_notes'].strip()}
**Last updated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

---

"""
        brain_file.write_text(header)

    # Format the entry block
    block = f"""## {entry['title']}

**Source:** {entry['source_type']} | **Date:** {entry.get('date', 'unknown')}
**URL:** {entry.get('url', 'n/a')}
**Tags:** {', '.join(entry.get('tags', []))}

{entry['summary']}

---

"""
    if dry_run:
        log.info(f"[DRY RUN] Would append to {brain_file.name}:\n{block[:200]}...")
        return

    with open(brain_file, "a") as f:
        f.write(block)
    log.info(f"Appended '{entry['title']}' to {brain_file.name}")

def summarise_and_append(
    brain_slug: str,
    brain_config: dict,
    text: str,
    *,
    title: str,
    source_type: str,
    url: str,
    date: str,
    dry_run: bool = False,
) -> bool:
    """Chunk text, summarise the first chunk, and append the entry to the brain file.

    Returns True if an entry was appended, False if the text was too short.
    """
    chunks = chunk_text(text)
    if not chunks:
        return False
    summary_data = summarise_chunk(chunks[0], brain_config)
    entry = {
        "title": title,
        "source_type": source_type,
        "url": url,
        "date": date,
        "summary": summary_data["summary"],
        "tags": summary_data["tags"],
    }
    append_to_brain(brain_slug, brain_config, entry, dry_run)
    return True

# ── GitHub commit ─────────────────────────────────────────────────────────────

def commit_to_github(updated_files: list[str]):
    """Push updated brain files to GitHub via API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.info("GitHub credentials not set — skipping commit")
        return

    api = f"https://api.github.com/repos/{GITHUB_REPO}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    for file_path in updated_files:
        path = Path(file_path)
        rel_path = path.relative_to(ROOT)
        content = path.read_bytes()
        import base64
        b64_content = base64.b64encode(content).decode()

        # Get current SHA (needed for update)
        sha = None
        r = requests.get(f"{api}/contents/{rel_path}", headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")

        payload = {
            "message": f"brain update: {path.name} [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC]",
            "content": b64_content,
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(f"{api}/contents/{rel_path}", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            log.info(f"Committed {rel_path} to GitHub")
        else:
            log.error(f"GitHub commit failed for {rel_path}: {r.text}")

# ── Main ingestion logic ──────────────────────────────────────────────────────

def process_videos(videos: list, yt_state: dict, brain_slug: str, brain_config: dict,
                   source_type: str, dry_run: bool = False) -> int:
    """Ingest unseen videos: fetch transcript, summarise, append. Returns count ingested."""
    count = 0
    for video in videos:
        vid_id = video["video_id"]
        if vid_id in yt_state["seen"]:
            continue
        transcript = get_transcript(vid_id)
        if not transcript:
            yt_state["seen"][vid_id] = True
            continue
        summarise_and_append(
            brain_slug,
            brain_config,
            transcript,
            title=video["title"],
            source_type=source_type,
            url=f"https://youtube.com/watch?v={vid_id}",
            date=video["published"][:10],
            dry_run=dry_run,
        )
        yt_state["seen"][vid_id] = True
        count += 1
        time.sleep(0.5)  # be gentle with APIs
    return count


def ingest_brain(brain_slug: str, brain_config: dict, state: dict, dry_run: bool = False) -> list[str]:
    """Run forward + backfill sweep for one brain. Returns list of updated file paths."""
    log.info(f"--- Ingesting brain: {brain_config['display_name']} ---")
    brain_state = get_brain_state(state, brain_slug)
    updated_files = []
    sources = brain_config.get("sources", {})

    # ── YouTube ──────────────────────────────────────────────────────────────
    for yt_source in sources.get("youtube", []):
        handle = yt_source["handle"]
        log.info(f"YouTube: resolving {handle}")

        try:
            channel_id = resolve_yt_handle(handle)
        except Exception as e:
            log.error(f"Could not resolve {handle}: {e}")
            continue

        yt_state = brain_state["youtube"].setdefault(handle, {
            "seen": {},
            "backfill_page_token": None,
            "backfill_done": False,
            "first_run_done": False,
        })

        # Forward sweep — get latest videos
        log.info(f"Forward sweep for {handle}")
        videos, _ = get_channel_videos(channel_id, max_results=FORWARD_LIMIT)
        new_count = process_videos(videos, yt_state, brain_slug, brain_config,
                                   "YouTube", dry_run)

        if not yt_state["first_run_done"]:
            yt_state["first_run_done"] = True
        log.info(f"Forward sweep: {new_count} new videos for {handle}")

        # Backfill sweep — work backward through older videos
        if not yt_state.get("backfill_done"):
            log.info(f"Backfill sweep for {handle}")
            page_token = yt_state.get("backfill_page_token")
            # On first run, skip first page (already covered by forward sweep)
            if page_token is None and yt_state["first_run_done"]:
                _, page_token = get_channel_videos(channel_id, max_results=50)
            if page_token:
                videos_bf, next_token = get_channel_videos(
                    channel_id, max_results=BACKFILL_BATCH, page_token=page_token
                )
                bf_count = process_videos(videos_bf, yt_state, brain_slug, brain_config,
                                          "YouTube (backfill)", dry_run)
                yt_state["backfill_page_token"] = next_token
                if not next_token:
                    yt_state["backfill_done"] = True
                    log.info(f"Backfill complete for {handle}")
                log.info(f"Backfill: {bf_count} videos this run for {handle}")

        brain_file = BRAINS_DIR / f"{brain_slug}.md"
        if brain_file.exists() and new_count > 0:
            updated_files.append(str(brain_file))

    # ── Substack ─────────────────────────────────────────────────────────────
    for ss_source in sources.get("substack", []):
        url = ss_source["url"]
        source_type = ss_source.get("type", "rss")
        log.info(f"Substack: fetching {ss_source['name']}")

        ss_seen = brain_state["substack"]

        if source_type == "web_scrape":
            # For non-standard RSS like The Batch — scrape index page
            posts = scrape_batch_newsletter(url, ss_seen)
        else:
            posts = get_substack_posts(url, ss_seen)

        new_count = 0
        for post in posts[:FORWARD_LIMIT]:
            if not post.get("text"):
                ss_seen[post["url"]] = True
                continue
            appended = summarise_and_append(
                brain_slug,
                brain_config,
                post["text"],
                title=post["title"],
                source_type=f"Substack ({ss_source['name']})",
                url=post["url"],
                date=post.get("published", "")[:10],
                dry_run=dry_run,
            )
            ss_seen[post["url"]] = True
            if not appended:
                continue
            new_count += 1
            time.sleep(0.5)

        log.info(f"Substack: {new_count} new posts from {ss_source['name']}")
        brain_file = BRAINS_DIR / f"{brain_slug}.md"
        if brain_file.exists() and new_count > 0:
            updated_files.append(str(brain_file))

    # ── Articles ─────────────────────────────────────────────────────────────
    for article in sources.get("articles", []):
        url = article["url"]
        if url in brain_state["articles"]:
            continue
        log.info(f"Article: scraping {url}")
        text = scrape_article(url)
        if not text:
            continue
        appended = summarise_and_append(
            brain_slug,
            brain_config,
            text,
            title=article.get("name", url),
            source_type="Article",
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            dry_run=dry_run,
        )
        if not appended:
            continue
        brain_state["articles"][url] = True
        brain_file = BRAINS_DIR / f"{brain_slug}.md"
        if brain_file.exists():
            updated_files.append(str(brain_file))

    brain_state["last_run"] = datetime.now(timezone.utc).isoformat()
    return list(set(updated_files))  # deduplicate


def scrape_batch_newsletter(base_url: str, already_seen: dict) -> list:
    """Scrape The Batch newsletter index from deeplearning.ai."""
    posts = []
    try:
        soup = fetch_soup(base_url)
        links = soup.find_all("a", href=True)
        article_urls = list({
            a["href"] for a in links
            if "/the-batch/" in a["href"] and a["href"] not in already_seen
            and len(a["href"]) > len("/the-batch/")
        })[:FORWARD_LIMIT]
        for url in article_urls:
            full_url = url if url.startswith("http") else f"https://www.deeplearning.ai{url}"
            text = scrape_article(full_url)
            posts.append({
                "url": full_url,
                "title": full_url.split("/")[-1].replace("-", " ").title(),
                "published": "",
                "text": text or "",
            })
    except Exception as e:
        log.error(f"Could not scrape The Batch: {e}")
    return posts


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Brain ingestion script")
    parser.add_argument("--brain", help="Only run this brain slug (e.g. nate_jones)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write anything")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG_FILE.read_text())
    state = load_state()
    all_updated = []

    brains = config["brains"]
    if args.brain:
        if args.brain not in brains:
            log.error(f"Brain '{args.brain}' not found in brains.yaml")
            sys.exit(1)
        brains = {args.brain: brains[args.brain]}

    for slug, brain_cfg in brains.items():
        try:
            updated = ingest_brain(slug, brain_cfg, state, dry_run=args.dry_run)
            all_updated.extend(updated)
        except Exception as e:
            log.error(f"Failed to ingest brain '{slug}': {e}", exc_info=True)

    if not args.dry_run:
        save_state(state)
        if all_updated:
            commit_to_github(all_updated)
            commit_to_github([str(STATE_FILE)])

    log.info(f"Done. Updated files: {all_updated or 'none'}")


if __name__ == "__main__":
    main()
