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
import base64
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

# ── Errors ────────────────────────────────────────────────────────────────────

class IngestionError(Exception):
    """Base class for ingestion failures."""

class TransientError(IngestionError):
    """A failure that may succeed on a later run (network, rate limit, 5xx).

    Items failing this way are deliberately not marked as seen, so the next run
    retries them.
    """

class PermanentError(IngestionError):
    """A failure that will not resolve by retrying (no transcript, 404)."""

class ConfigError(IngestionError):
    """Invalid configuration or rejected credentials."""


def classify_request_error(exc: Exception) -> IngestionError:
    """Map an HTTP/parse failure onto a transient, permanent or config error."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status in (401, 403):
            return ConfigError(f"HTTP {status} (credentials rejected): {exc}")
        if status == 429 or status >= 500:
            return TransientError(f"HTTP {status}: {exc}")
        return PermanentError(f"HTTP {status}: {exc}")
    if isinstance(exc, requests.RequestException):
        return TransientError(str(exc) or exc.__class__.__name__)
    return TransientError(f"{exc.__class__.__name__}: {exc}")

# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"State file {STATE_FILE} is not valid JSON ({e}). Fix or delete it before rerunning "
            "(deleting it makes the next run re-ingest everything)."
        ) from e
    if not isinstance(state, dict):
        raise ConfigError(f"State file {STATE_FILE} must contain a JSON object, got {type(state).__name__}")
    return state

def save_state(state: dict):
    """Write state atomically so a crash mid-write cannot corrupt it."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)

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
        raise ConfigError("YOUTUBE_API_KEY not set")
    handle_clean = handle.lstrip("@")
    url = "https://www.googleapis.com/youtube/v3/channels"
    try:
        resp = requests.get(url, params={
            "part": "id",
            "forHandle": handle_clean,
            "key": YT_API_KEY,
        }, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except (requests.RequestException, ValueError) as e:
        raise classify_request_error(e) from e
    if not items:
        raise PermanentError(f"Could not resolve YouTube handle: {handle}")
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
    if not YT_API_KEY:
        raise ConfigError("YOUTUBE_API_KEY not set")
    if page_token:
        params["pageToken"] = page_token
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise classify_request_error(e) from e
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

def get_transcript(video_id: str) -> str:
    """Fetch transcript text for a video.

    Raises PermanentError when the video has no usable transcript, and
    TransientError for fetch failures that are worth retrying.
    """
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"])
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        raise PermanentError(f"no transcript available: {e}") from e
    except Exception as e:
        raise TransientError(f"transcript fetch failed: {e.__class__.__name__}: {e}") from e
    text = " ".join(t["text"] for t in transcript).strip()
    if not text:
        raise PermanentError("transcript is empty")
    return text

# ── Substack helpers ──────────────────────────────────────────────────────────

def get_substack_posts(base_url: str, already_seen: dict) -> list:
    """Fetch new posts from a Substack via its RSS feed."""
    rss_url = base_url.rstrip("/") + "/feed"
    feed = feedparser.parse(rss_url)
    # feedparser never raises: it signals failure via bozo/bozo_exception plus an
    # empty entry list, which otherwise looks identical to "no new posts".
    if getattr(feed, "bozo", 0) and not feed.entries:
        raise TransientError(
            f"could not parse feed {rss_url}: {getattr(feed, 'bozo_exception', 'unknown error')}"
        )
    posts = []
    for entry in feed.entries:
        url = entry.get("link", "")
        if not url:
            log.warning(f"Feed entry without link in {rss_url}: {entry.get('title', 'untitled')!r}")
            continue
        if url in already_seen:
            continue
        # fetch full content
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Substack puts article content in .available-content or article tag
            content_el = soup.find("div", class_="available-content") or soup.find("article")
            text = content_el.get_text(separator=" ", strip=True) if content_el else ""
            if not text:
                log.warning(f"No article body at {url} — falling back to feed summary")
                text = entry.get("summary", "")
        except requests.RequestException as e:
            log.warning(f"Could not fetch substack post {url} ({e}) — falling back to feed summary")
            text = entry.get("summary", "")
        posts.append({
            "url": url,
            "title": entry.get("title", "Untitled"),
            "published": entry.get("published", ""),
            "text": text,
        })
    return posts

# ── Web article helpers ───────────────────────────────────────────────────────

def scrape_article(url: str) -> str:
    """Simple article scraper - extracts main text content.

    Raises instead of returning None so callers can tell "fetch failed, retry
    later" apart from "page has no article body".
    """
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        raise classify_request_error(e) from e
    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove nav, header, footer, scripts
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()
    # Try article tag first, then main, then body
    content = soup.find("article") or soup.find("main") or soup.find("body")
    text = content.get_text(separator=" ", strip=True) if content else ""
    if not text:
        raise PermanentError(f"no extractable text at {url}")
    return text

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
    """Send a chunk to OpenRouter and get summary + tags back.

    Raises on failure rather than writing a truncated placeholder: a placeholder
    entry is marked as seen and never revisited, permanently degrading the brain.
    Truncation is only used in the explicit no-API-key mode.
    """
    if not OPENROUTER_KEY:
        # Explicitly configured fallback: first 300 chars as summary, no tags
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
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, ValueError) as e:
        raise classify_request_error(e) from e
    except (KeyError, IndexError, TypeError) as e:
        raise TransientError(f"unexpected OpenRouter response shape: {e!r}") from e

    # Strip markdown fences if present
    content = content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise TransientError(f"model did not return JSON ({e}): {content[:200]!r}") from e
    if not isinstance(parsed, dict) or not parsed.get("summary"):
        raise TransientError(f"model response missing 'summary': {content[:200]!r}")
    tags = parsed.get("tags")
    if not isinstance(tags, list):
        tags = []
    return {"summary": parsed["summary"], "tags": [str(t) for t in tags]}


def summarise_first_chunk(text: str, brain_config: dict) -> dict:
    """Chunk text and summarise the representative first chunk."""
    chunks = chunk_text(text)
    if not chunks:
        raise PermanentError("content too short to produce a chunk")
    return summarise_chunk(chunks[0], brain_config)

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

# ── GitHub commit ─────────────────────────────────────────────────────────────

def commit_to_github(updated_files: list[str]):
    """Push updated brain files to GitHub via API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.info("GitHub credentials not set — skipping commit")
        return
    failures = []

    api = f"https://api.github.com/repos/{GITHUB_REPO}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    for file_path in updated_files:
        path = Path(file_path)
        rel_path = path.relative_to(ROOT)
        content = path.read_bytes()
        b64_content = base64.b64encode(content).decode()

        # Get current SHA (needed for update). Treating anything but 404 as "new
        # file" would push a blind create that fails or clobbers the remote file.
        sha = None
        try:
            r = requests.get(f"{api}/contents/{rel_path}", headers=headers, timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha")
            elif r.status_code != 404:
                failures.append(f"{rel_path}: cannot read current SHA (HTTP {r.status_code}: {r.text[:200]})")
                continue
        except requests.RequestException as e:
            failures.append(f"{rel_path}: cannot read current SHA ({e})")
            continue

        payload = {
            "message": f"brain update: {path.name} [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC]",
            "content": b64_content,
        }
        if sha:
            payload["sha"] = sha

        try:
            r = requests.put(f"{api}/contents/{rel_path}", headers=headers, json=payload, timeout=15)
        except requests.RequestException as e:
            failures.append(f"{rel_path}: commit request failed ({e})")
            continue
        if r.status_code in (200, 201):
            log.info(f"Committed {rel_path} to GitHub")
        else:
            failures.append(f"{rel_path}: HTTP {r.status_code}: {r.text[:200]}")

    if failures:
        raise IngestionError("GitHub commit failed for: " + "; ".join(failures))

# ── Main ingestion logic ──────────────────────────────────────────────────────

def process_video(brain_slug: str, brain_config: dict, video: dict, source_type: str, dry_run: bool):
    """Ingest one video. Raises IngestionError if it cannot be processed."""
    vid_id = video["video_id"]
    summary_data = summarise_first_chunk(get_transcript(vid_id), brain_config)
    append_to_brain(brain_slug, brain_config, {
        "title": video["title"],
        "source_type": source_type,
        "url": f"https://youtube.com/watch?v={vid_id}",
        "date": video["published"][:10],
        "summary": summary_data["summary"],
        "tags": summary_data["tags"],
    }, dry_run)


def ingest_brain(brain_slug: str, brain_config: dict, state: dict,
                 dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Run forward + backfill sweep for one brain.

    Returns (updated file paths, retryable failure descriptions). Failures are
    returned rather than logged-and-forgotten so the caller can exit non-zero.
    ConfigError propagates: retrying other sources with bad credentials is waste.
    """
    log.info(f"--- Ingesting brain: {brain_config['display_name']} ---")
    brain_state = get_brain_state(state, brain_slug)
    updated_files = []
    errors: list[str] = []
    sources = brain_config.get("sources", {})

    def note_new_content(count: int):
        brain_file = BRAINS_DIR / f"{brain_slug}.md"
        if count > 0 and brain_file.exists():
            updated_files.append(str(brain_file))

    # ── YouTube ──────────────────────────────────────────────────────────────
    for yt_source in sources.get("youtube", []):
        handle = yt_source["handle"]
        log.info(f"YouTube: resolving {handle}")

        try:
            channel_id = resolve_yt_handle(handle)
        except ConfigError:
            raise
        except IngestionError as e:
            errors.append(f"youtube {handle}: could not resolve channel: {e}")
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
        try:
            videos, _ = get_channel_videos(channel_id, max_results=FORWARD_LIMIT)
        except ConfigError:
            raise
        except IngestionError as e:
            errors.append(f"youtube {handle}: forward sweep failed: {e}")
            log.error(f"Forward sweep failed for {handle}: {e}")
            continue
        new_count = 0
        for video in videos:
            vid_id = video["video_id"]
            if vid_id in yt_state["seen"]:
                continue
            try:
                # Summarises the first chunk, representative of the video
                process_video(brain_slug, brain_config, video, "YouTube", dry_run)
            except ConfigError:
                raise
            except PermanentError as e:
                log.warning(f"Skipping video {vid_id} permanently: {e}")
                yt_state["seen"][vid_id] = True
                continue
            except IngestionError as e:
                # Not marked as seen, so the next run retries it.
                errors.append(f"youtube {handle} video {vid_id}: {e}")
                log.error(f"Video {vid_id} failed, will retry next run: {e}")
                continue
            yt_state["seen"][vid_id] = True
            new_count += 1
            time.sleep(0.5)  # be gentle with APIs

        if not yt_state["first_run_done"]:
            yt_state["first_run_done"] = True
        log.info(f"Forward sweep: {new_count} new videos for {handle}")

        # Backfill sweep — work backward through older videos
        bf_count = 0
        if not yt_state.get("backfill_done"):
            log.info(f"Backfill sweep for {handle}")
            page_token = None
            videos_bf: list = []
            next_token = None
            try:
                page_token = yt_state.get("backfill_page_token")
                # On first run, skip first page (already covered by forward sweep)
                if page_token is None and yt_state["first_run_done"]:
                    _, page_token = get_channel_videos(channel_id, max_results=50)
                if page_token:
                    videos_bf, next_token = get_channel_videos(
                        channel_id, max_results=BACKFILL_BATCH, page_token=page_token
                    )
            except ConfigError:
                raise
            except IngestionError as e:
                errors.append(f"youtube {handle}: backfill sweep failed: {e}")
                log.error(f"Backfill sweep failed for {handle}: {e}")
                page_token = None

            if page_token:
                for video in videos_bf:
                    vid_id = video["video_id"]
                    if vid_id in yt_state["seen"]:
                        continue
                    try:
                        process_video(brain_slug, brain_config, video, "YouTube (backfill)", dry_run)
                    except ConfigError:
                        raise
                    except PermanentError as e:
                        log.warning(f"Skipping backfill video {vid_id} permanently: {e}")
                        yt_state["seen"][vid_id] = True
                        continue
                    except IngestionError as e:
                        errors.append(f"youtube {handle} backfill video {vid_id}: {e}")
                        log.error(f"Backfill video {vid_id} failed, will retry next run: {e}")
                        continue
                    yt_state["seen"][vid_id] = True
                    bf_count += 1
                    time.sleep(0.5)
                yt_state["backfill_page_token"] = next_token
                if not next_token:
                    yt_state["backfill_done"] = True
                    log.info(f"Backfill complete for {handle}")
                log.info(f"Backfill: {bf_count} videos this run for {handle}")

        # Backfill-only additions need committing too, not just forward ones.
        note_new_content(new_count + bf_count)

    # ── Substack ─────────────────────────────────────────────────────────────
    for ss_source in sources.get("substack", []):
        url = ss_source["url"]
        source_type = ss_source.get("type", "rss")
        log.info(f"Substack: fetching {ss_source['name']}")

        ss_seen = brain_state["substack"]

        try:
            if source_type == "web_scrape":
                # For non-standard RSS like The Batch — scrape index page
                posts = scrape_batch_newsletter(url, ss_seen)
            else:
                posts = get_substack_posts(url, ss_seen)
        except ConfigError:
            raise
        except IngestionError as e:
            errors.append(f"substack {ss_source['name']}: could not list posts: {e}")
            log.error(f"Could not list posts for {ss_source['name']}: {e}")
            continue

        new_count = 0
        for post in posts[:FORWARD_LIMIT]:
            if not post.get("text"):
                log.warning(f"No text for post {post['url']} — marking as seen")
                ss_seen[post["url"]] = True
                continue
            try:
                summary_data = summarise_first_chunk(post["text"], brain_config)
            except ConfigError:
                raise
            except PermanentError as e:
                log.warning(f"Skipping post {post['url']} permanently: {e}")
                ss_seen[post["url"]] = True
                continue
            except IngestionError as e:
                errors.append(f"substack post {post['url']}: {e}")
                log.error(f"Post {post['url']} failed, will retry next run: {e}")
                continue
            entry = {
                "title": post["title"],
                "source_type": f"Substack ({ss_source['name']})",
                "url": post["url"],
                "date": post.get("published", "")[:10],
                "summary": summary_data["summary"],
                "tags": summary_data["tags"],
            }
            append_to_brain(brain_slug, brain_config, entry, dry_run)
            ss_seen[post["url"]] = True
            new_count += 1
            time.sleep(0.5)

        log.info(f"Substack: {new_count} new posts from {ss_source['name']}")
        note_new_content(new_count)

    # ── Articles ─────────────────────────────────────────────────────────────
    for article in sources.get("articles", []):
        url = article["url"]
        if url in brain_state["articles"]:
            continue
        log.info(f"Article: scraping {url}")
        try:
            summary_data = summarise_first_chunk(scrape_article(url), brain_config)
        except ConfigError:
            raise
        except PermanentError as e:
            log.warning(f"Skipping article {url} permanently: {e}")
            brain_state["articles"][url] = True
            continue
        except IngestionError as e:
            errors.append(f"article {url}: {e}")
            log.error(f"Article {url} failed, will retry next run: {e}")
            continue
        entry = {
            "title": article.get("name", url),
            "source_type": "Article",
            "url": url,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": summary_data["summary"],
            "tags": summary_data["tags"],
        }
        append_to_brain(brain_slug, brain_config, entry, dry_run)
        brain_state["articles"][url] = True
        note_new_content(1)

    brain_state["last_run"] = datetime.now(timezone.utc).isoformat()
    return list(set(updated_files)), errors  # deduplicate


def scrape_batch_newsletter(base_url: str, already_seen: dict) -> list:
    """Scrape The Batch newsletter index from deeplearning.ai."""
    try:
        resp = requests.get(base_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        raise classify_request_error(e) from e
    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.find_all("a", href=True)
    article_urls = list({
        a["href"] for a in links
        if "/the-batch/" in a["href"] and a["href"] not in already_seen
        and len(a["href"]) > len("/the-batch/")
    })[:FORWARD_LIMIT]
    posts = []
    for url in article_urls:
        full_url = url if url.startswith("http") else f"https://www.deeplearning.ai{url}"
        try:
            text = scrape_article(full_url)
        except ConfigError:
            raise
        except IngestionError as e:
            # One unreachable article shouldn't drop the whole index.
            log.warning(f"Could not scrape {full_url}: {e}")
            continue
        posts.append({
            "url": full_url,
            "title": full_url.split("/")[-1].replace("-", " ").title(),
            "published": "",
            "text": text,
        })
    return posts


# ── Entry point ───────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load and validate brains.yaml, returning the brains mapping."""
    try:
        config = yaml.safe_load(CONFIG_FILE.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"Config file {CONFIG_FILE} not found") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"Config file {CONFIG_FILE} is not valid YAML: {e}") from e
    if not isinstance(config, dict) or not isinstance(config.get("brains"), dict):
        raise ConfigError(f"Config file {CONFIG_FILE} must define a top-level 'brains' mapping")
    for slug, cfg in config["brains"].items():
        missing = [k for k in ("display_name", "expertise_tags", "style_notes") if k not in cfg]
        if missing:
            raise ConfigError(f"Brain '{slug}' in {CONFIG_FILE} is missing: {', '.join(missing)}")
    return config["brains"]


def main():
    parser = argparse.ArgumentParser(description="Brain ingestion script")
    parser.add_argument("--brain", help="Only run this brain slug (e.g. nate_jones)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write anything")
    args = parser.parse_args()

    try:
        brains = load_config()
        state = load_state()
    except ConfigError as e:
        log.error(str(e))
        sys.exit(2)

    all_updated: list[str] = []
    failures: list[str] = []

    if args.brain:
        if args.brain not in brains:
            log.error(f"Brain '{args.brain}' not found in brains.yaml")
            sys.exit(2)
        brains = {args.brain: brains[args.brain]}

    for slug, brain_cfg in brains.items():
        try:
            updated, errors = ingest_brain(slug, brain_cfg, state, dry_run=args.dry_run)
            all_updated.extend(updated)
            failures.extend(errors)
        except ConfigError as e:
            # Bad credentials or config affect every brain — stop instead of
            # burning through the remaining ones.
            log.error(f"Configuration error while ingesting '{slug}': {e}")
            if not args.dry_run:
                save_state(state)
            sys.exit(2)
        except Exception as e:
            failures.append(f"brain {slug}: {e}")
            log.error(f"Failed to ingest brain '{slug}': {e}", exc_info=True)

    if not args.dry_run:
        # State is persisted even on partial failure so successful items aren't
        # re-ingested; commit failures surface through the exit code.
        save_state(state)
        if all_updated:
            try:
                commit_to_github(all_updated + [str(STATE_FILE)])
            except IngestionError as e:
                failures.append(str(e))
                log.error(str(e))

    log.info(f"Done. Updated files: {all_updated or 'none'}")

    if failures:
        log.error(f"{len(failures)} failure(s) during ingestion:")
        for failure in failures:
            log.error(f"  - {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
