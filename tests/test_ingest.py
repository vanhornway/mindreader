import json

import pytest
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled

from conftest import BRAIN_CONFIG, DummyResponse


# ── State management ──────────────────────────────────────────────────────────

def test_load_state_returns_empty_dict_when_file_missing(ingest):
    assert ingest.load_state() == {}


def test_save_state_creates_parent_dir_and_roundtrips(ingest):
    ingest.save_state({"ada": {"last_run": "2026-01-01"}})
    assert ingest.STATE_FILE.exists()
    assert ingest.load_state() == {"ada": {"last_run": "2026-01-01"}}


def test_get_brain_state_creates_default_and_is_stable(ingest):
    state = {}
    brain_state = ingest.get_brain_state(state, "ada")
    assert brain_state == {
        "youtube": {},
        "substack": {},
        "articles": {},
        "backfill_cursor": {},
        "last_run": None,
    }
    brain_state["youtube"]["v1"] = True
    assert ingest.get_brain_state(state, "ada")["youtube"] == {"v1": True}


# ── YouTube helpers ───────────────────────────────────────────────────────────

def test_resolve_yt_handle_strips_at_and_returns_id(ingest, monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return DummyResponse({"items": [{"id": "UC123"}]})

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    assert ingest.resolve_yt_handle("@AdaLovelace") == "UC123"
    assert captured["params"]["forHandle"] == "AdaLovelace"


def test_resolve_yt_handle_without_api_key_raises(ingest, monkeypatch):
    monkeypatch.setattr(ingest, "YT_API_KEY", "")
    with pytest.raises(RuntimeError, match="YOUTUBE_API_KEY"):
        ingest.resolve_yt_handle("@AdaLovelace")


def test_resolve_yt_handle_unresolvable_raises_value_error(ingest, monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse({"items": []}))
    with pytest.raises(ValueError, match="Could not resolve"):
        ingest.resolve_yt_handle("@nobody")


def test_get_channel_videos_maps_fields_and_skips_non_videos(ingest, monkeypatch):
    payload = {
        "items": [
            {
                "id": {"videoId": "v1"},
                "snippet": {
                    "title": "First",
                    "publishedAt": "2026-01-02T03:04:05Z",
                    "channelTitle": "Ada",
                },
            },
            {"id": {"playlistId": "p1"}, "snippet": {}},
        ],
        "nextPageToken": "TOKEN2",
    }
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse(payload))
    videos, token = ingest.get_channel_videos("UC123")
    assert videos == [
        {
            "video_id": "v1",
            "title": "First",
            "published": "2026-01-02T03:04:05Z",
            "channel": "Ada",
        }
    ]
    assert token == "TOKEN2"


def test_get_channel_videos_passes_page_token(ingest, monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        return DummyResponse({"items": []})

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    videos, token = ingest.get_channel_videos("UC123", max_results=5, page_token="ABC")
    assert (videos, token) == ([], None)
    assert captured["pageToken"] == "ABC"
    assert captured["maxResults"] == 5


def test_get_transcript_joins_segments(ingest, monkeypatch):
    monkeypatch.setattr(
        ingest.YouTubeTranscriptApi,
        "get_transcript",
        staticmethod(lambda vid, languages=None: [{"text": "hello"}, {"text": "world"}]),
    )
    assert ingest.get_transcript("v1") == "hello world"


@pytest.mark.parametrize(
    "error",
    [
        TranscriptsDisabled("v1"),
        NoTranscriptFound("v1", ["en"], {}),
        RuntimeError("boom"),
    ],
)
def test_get_transcript_returns_none_on_errors(ingest, monkeypatch, error):
    def raise_error(vid, languages=None):
        raise error

    monkeypatch.setattr(ingest.YouTubeTranscriptApi, "get_transcript", staticmethod(raise_error))
    assert ingest.get_transcript("v1") is None


# ── Substack helpers ──────────────────────────────────────────────────────────

def test_get_substack_posts_extracts_content_and_skips_seen(ingest, monkeypatch):
    feed_entries = [
        {"link": "https://blog.test/new", "title": "New", "published": "2026-01-01", "summary": "sum"},
        {"link": "https://blog.test/old", "title": "Old", "published": "2025-01-01"},
    ]
    captured = {}

    def fake_parse(url):
        captured["rss_url"] = url
        return type("Feed", (), {"entries": feed_entries})()

    html = '<html><div class="available-content"><p>Body text</p></div></html>'
    monkeypatch.setattr(ingest.feedparser, "parse", fake_parse)
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse(text=html))

    posts = ingest.get_substack_posts("https://blog.test/", {"https://blog.test/old": True})
    assert captured["rss_url"] == "https://blog.test/feed"
    assert posts == [
        {
            "url": "https://blog.test/new",
            "title": "New",
            "published": "2026-01-01",
            "text": "Body text",
        }
    ]


def test_get_substack_posts_falls_back_to_article_tag(ingest, monkeypatch):
    monkeypatch.setattr(
        ingest.feedparser,
        "parse",
        lambda url: type("Feed", (), {"entries": [{"link": "u", "title": "T"}]})(),
    )
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: DummyResponse(text="<article>Article body</article>")
    )
    assert ingest.get_substack_posts("https://blog.test", {})[0]["text"] == "Article body"


def test_get_substack_posts_uses_summary_when_fetch_fails(ingest, monkeypatch):
    entry = {"link": "u", "title": "T", "summary": "fallback summary"}
    monkeypatch.setattr(
        ingest.feedparser, "parse", lambda url: type("Feed", (), {"entries": [entry]})()
    )

    def boom(*a, **k):
        raise ingest.requests.RequestException("network down")

    monkeypatch.setattr(ingest.requests, "get", boom)
    posts = ingest.get_substack_posts("https://blog.test", {})
    assert posts[0]["text"] == "fallback summary"


def test_get_substack_posts_empty_text_when_no_content_element(ingest, monkeypatch):
    monkeypatch.setattr(
        ingest.feedparser,
        "parse",
        lambda url: type("Feed", (), {"entries": [{"link": "u"}]})(),
    )
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse(text="<div>x</div>"))
    post = ingest.get_substack_posts("https://blog.test", {})[0]
    assert post["text"] == ""
    assert post["title"] == "Untitled"


# ── Article scraping ──────────────────────────────────────────────────────────

def test_scrape_article_strips_boilerplate_tags(ingest, monkeypatch):
    html = (
        "<html><body><nav>Nav</nav><header>Header</header>"
        "<article><p>Real content</p><script>evil()</script></article>"
        "<footer>Footer</footer></body></html>"
    )
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse(text=html))
    assert ingest.scrape_article("https://x.test") == "Real content"


def test_scrape_article_falls_back_to_main_then_body(ingest, monkeypatch):
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: DummyResponse(text="<main>Main text</main>")
    )
    assert ingest.scrape_article("https://x.test") == "Main text"


def test_scrape_article_returns_none_on_error(ingest, monkeypatch):
    def boom(*a, **k):
        raise ingest.requests.RequestException("timeout")

    monkeypatch.setattr(ingest.requests, "get", boom)
    assert ingest.scrape_article("https://x.test") is None


def test_scrape_batch_newsletter_dedupes_and_absolutises_urls(ingest, monkeypatch):
    html = (
        '<a href="/the-batch/issue-1">1</a>'
        '<a href="/the-batch/issue-1">dup</a>'
        '<a href="/the-batch/">index</a>'
        '<a href="/other/page">other</a>'
    )
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: DummyResponse(text=html))
    monkeypatch.setattr(ingest, "scrape_article", lambda url: "text of " + url)
    posts = ingest.scrape_batch_newsletter("https://www.deeplearning.ai/the-batch/", {})
    assert posts == [
        {
            "url": "https://www.deeplearning.ai/the-batch/issue-1",
            "title": "Issue 1",
            "published": "",
            "text": "text of https://www.deeplearning.ai/the-batch/issue-1",
        }
    ]


def test_scrape_batch_newsletter_skips_seen_and_survives_errors(ingest, monkeypatch):
    monkeypatch.setattr(
        ingest.requests,
        "get",
        lambda *a, **k: DummyResponse(text='<a href="/the-batch/seen">s</a>'),
    )
    assert ingest.scrape_batch_newsletter("https://x.test", {"/the-batch/seen": True}) == []

    def boom(*a, **k):
        raise ingest.requests.RequestException("nope")

    monkeypatch.setattr(ingest.requests, "get", boom)
    assert ingest.scrape_batch_newsletter("https://x.test", {}) == []


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_chunk_text_applies_overlap_and_drops_tiny_tail(ingest):
    words = [f"w{i}" for i in range(120)]
    chunks = ingest.chunk_text(" ".join(words), chunk_words=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0].split()[:3] == ["w0", "w1", "w2"]
    assert len(chunks[0].split()) == 100

    chunks = ingest.chunk_text(" ".join(words), chunk_words=60, overlap=10)
    assert len(chunks) == 2
    assert chunks[1].split()[0] == "w50"


def test_chunk_text_drops_chunks_of_50_words_or_fewer(ingest):
    assert ingest.chunk_text(" ".join(["w"] * 50)) == []
    assert len(ingest.chunk_text(" ".join(["w"] * 51))) == 1


def test_chunk_text_on_empty_input(ingest):
    assert ingest.chunk_text("") == []


# ── Summarisation ─────────────────────────────────────────────────────────────

def test_summarise_chunk_without_api_key_truncates(ingest):
    chunk = "x" * 400
    assert ingest.summarise_chunk(chunk, BRAIN_CONFIG) == {
        "summary": "x" * 300 + "...",
        "tags": [],
    }


def test_summarise_chunk_parses_fenced_json_and_sends_expected_payload(ingest, monkeypatch):
    monkeypatch.setattr(ingest, "OPENROUTER_KEY", "or-key")
    monkeypatch.setattr(ingest, "OPENROUTER_MODEL", "test/model")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        content = '```json\n{"summary": "A summary", "tags": ["math"]}\n```'
        return DummyResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(ingest.requests, "post", fake_post)
    result = ingest.summarise_chunk("some text", BRAIN_CONFIG)
    assert result == {"summary": "A summary", "tags": ["math"]}
    assert captured["headers"]["Authorization"] == "Bearer or-key"
    assert captured["json"]["model"] == "test/model"
    assert "Ada Lovelace" in captured["json"]["messages"][0]["content"]


def test_summarise_chunk_falls_back_on_bad_json(ingest, monkeypatch):
    monkeypatch.setattr(ingest, "OPENROUTER_KEY", "or-key")
    monkeypatch.setattr(
        ingest.requests,
        "post",
        lambda *a, **k: DummyResponse({"choices": [{"message": {"content": "not json"}}]}),
    )
    assert ingest.summarise_chunk("chunk text", BRAIN_CONFIG)["tags"] == []


def test_summarise_chunk_falls_back_on_http_error(ingest, monkeypatch):
    monkeypatch.setattr(ingest, "OPENROUTER_KEY", "or-key")
    monkeypatch.setattr(ingest.requests, "post", lambda *a, **k: DummyResponse(status_code=500))
    assert ingest.summarise_chunk("chunk text", BRAIN_CONFIG) == {
        "summary": "chunk text...",
        "tags": [],
    }


# ── Brain file writer ─────────────────────────────────────────────────────────

ENTRY = {
    "title": "Notes on the Analytical Engine",
    "source_type": "Article",
    "url": "https://x.test/a",
    "date": "2026-01-01",
    "summary": "A summary.",
    "tags": ["math", "computing"],
}


def test_append_to_brain_writes_header_then_entry(ingest):
    ingest.append_to_brain("ada", BRAIN_CONFIG, ENTRY)
    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert content.startswith("# Ada Lovelace — Knowledge Brain")
    assert "**Expertise:** math, computing" in content
    assert "**Style:** Precise and analytical." in content
    assert "## Notes on the Analytical Engine" in content
    assert "**Tags:** math, computing" in content


def test_append_to_brain_appends_without_duplicate_header(ingest):
    ingest.append_to_brain("ada", BRAIN_CONFIG, ENTRY)
    ingest.append_to_brain("ada", BRAIN_CONFIG, {**ENTRY, "title": "Second"})
    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert content.count("Knowledge Brain") == 1
    assert content.count("\n## ") == 2


def test_append_to_brain_defaults_missing_optional_fields(ingest):
    ingest.append_to_brain("ada", BRAIN_CONFIG, {"title": "T", "source_type": "Article", "summary": "S"})
    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert "**Date:** unknown" in content
    assert "**URL:** n/a" in content


def test_append_to_brain_dry_run_writes_nothing(ingest):
    ingest.append_to_brain("ada", BRAIN_CONFIG, ENTRY, dry_run=True)
    assert not (ingest.BRAINS_DIR / "ada.md").exists()


# ── GitHub commit ─────────────────────────────────────────────────────────────

def test_commit_to_github_skips_without_credentials(ingest, monkeypatch):
    called = []
    monkeypatch.setattr(ingest.requests, "put", lambda *a, **k: called.append(1))
    ingest.commit_to_github(["whatever"])
    assert called == []


def test_commit_to_github_includes_sha_when_file_exists(ingest, monkeypatch):
    monkeypatch.setattr(ingest, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(ingest, "GITHUB_REPO", "owner/repo")
    brain_file = ingest.BRAINS_DIR / "ada.md"
    brain_file.write_text("hello")
    puts = []

    monkeypatch.setattr(
        ingest.requests, "get", lambda url, headers=None, timeout=None: DummyResponse({"sha": "abc"})
    )

    def fake_put(url, headers=None, json=None, timeout=None):
        puts.append((url, json))
        return DummyResponse(status_code=200)

    monkeypatch.setattr(ingest.requests, "put", fake_put)
    ingest.commit_to_github([str(brain_file)])

    url, payload = puts[0]
    assert url == "https://api.github.com/repos/owner/repo/contents/brains/ada.md"
    assert payload["sha"] == "abc"
    assert payload["content"] == "aGVsbG8="


def test_commit_to_github_omits_sha_for_new_file_and_logs_failure(ingest, monkeypatch, caplog):
    monkeypatch.setattr(ingest, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(ingest, "GITHUB_REPO", "owner/repo")
    brain_file = ingest.BRAINS_DIR / "ada.md"
    brain_file.write_text("hello")
    puts = []

    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: DummyResponse(status_code=404)
    )

    def fake_put(url, headers=None, json=None, timeout=None):
        puts.append(json)
        return DummyResponse(text="conflict", status_code=409)

    monkeypatch.setattr(ingest.requests, "put", fake_put)
    with caplog.at_level("ERROR"):
        ingest.commit_to_github([str(brain_file)])

    assert "sha" not in puts[0]
    assert "GitHub commit failed" in caplog.text


# ── ingest_brain ──────────────────────────────────────────────────────────────

@pytest.fixture
def stub_pipeline(ingest, monkeypatch):
    """Stub out network calls used by ingest_brain."""
    monkeypatch.setattr(ingest, "resolve_yt_handle", lambda handle: "UC123")
    monkeypatch.setattr(
        ingest,
        "summarise_chunk",
        lambda chunk, cfg: {"summary": "summary!", "tags": ["math"]},
    )
    monkeypatch.setattr(ingest, "get_transcript", lambda vid: " ".join(["word"] * 200))
    return ingest


def _video(vid, published="2026-01-02T00:00:00Z"):
    return {"video_id": vid, "title": f"Video {vid}", "published": published, "channel": "Ada"}


def test_ingest_brain_youtube_forward_sweep_records_entries(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    monkeypatch.setattr(
        ingest, "get_channel_videos", lambda cid, max_results=50, page_token=None: ([_video("v1")], None)
    )
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada", "name": "Ada"}]}}
    state = {}

    updated = ingest.ingest_brain("ada", config, state)

    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert "## Video v1" in content
    assert "**Source:** YouTube |" in content
    assert "https://youtube.com/watch?v=v1" in content
    assert updated == [str(ingest.BRAINS_DIR / "ada.md")]
    yt_state = state["ada"]["youtube"]["@ada"]
    assert yt_state["seen"] == {"v1": True}
    assert yt_state["first_run_done"] is True
    assert state["ada"]["last_run"] is not None


def test_ingest_brain_youtube_skips_seen_and_transcriptless_videos(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    monkeypatch.setattr(
        ingest,
        "get_channel_videos",
        lambda cid, max_results=50, page_token=None: ([_video("seen"), _video("no_transcript")], None),
    )
    monkeypatch.setattr(ingest, "get_transcript", lambda vid: None)
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    state = {"ada": {
        "youtube": {"@ada": {
            "seen": {"seen": True},
            "backfill_page_token": None,
            "backfill_done": True,
            "first_run_done": True,
        }},
        "substack": {},
        "articles": {},
        "backfill_cursor": {},
        "last_run": None,
    }}

    assert ingest.ingest_brain("ada", config, state) == []
    assert not (ingest.BRAINS_DIR / "ada.md").exists()
    assert state["ada"]["youtube"]["@ada"]["seen"] == {"seen": True, "no_transcript": True}


def test_ingest_brain_backfill_sweep_advances_cursor(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    calls = []

    def fake_get_channel_videos(cid, max_results=50, page_token=None):
        calls.append((max_results, page_token))
        if page_token == "PAGE2":
            return [_video("old1")], "PAGE3"
        if page_token is None and max_results == ingest.FORWARD_LIMIT:
            return [], None
        return [], "PAGE2"

    monkeypatch.setattr(ingest, "get_channel_videos", fake_get_channel_videos)
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    state = {}

    ingest.ingest_brain("ada", config, state)

    yt_state = state["ada"]["youtube"]["@ada"]
    assert yt_state["backfill_page_token"] == "PAGE3"
    assert yt_state["backfill_done"] is False
    assert yt_state["seen"] == {"old1": True}
    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert "**Source:** YouTube (backfill) |" in content
    assert (ingest.BACKFILL_BATCH, "PAGE2") in calls


def test_ingest_brain_backfill_skips_seen_and_transcriptless_videos(stub_pipeline, monkeypatch):
    ingest = stub_pipeline

    def fake_get_channel_videos(cid, max_results=50, page_token=None):
        if page_token == "PAGE2":
            return [_video("seen"), _video("no_transcript")], None
        if max_results == ingest.FORWARD_LIMIT:
            return [], None
        return [], "PAGE2"

    monkeypatch.setattr(ingest, "get_channel_videos", fake_get_channel_videos)
    monkeypatch.setattr(ingest, "get_transcript", lambda vid: None)
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    state = {"ada": {
        "youtube": {"@ada": {
            "seen": {"seen": True},
            "backfill_page_token": None,
            "backfill_done": False,
            "first_run_done": True,
        }},
        "substack": {},
        "articles": {},
        "backfill_cursor": {},
        "last_run": None,
    }}

    assert ingest.ingest_brain("ada", config, state) == []
    assert not (ingest.BRAINS_DIR / "ada.md").exists()
    yt_state = state["ada"]["youtube"]["@ada"]
    assert yt_state["seen"] == {"seen": True, "no_transcript": True}
    assert yt_state["backfill_done"] is True


def test_ingest_brain_marks_backfill_done_when_no_next_token(stub_pipeline, monkeypatch):
    ingest = stub_pipeline

    def fake_get_channel_videos(cid, max_results=50, page_token=None):
        if page_token == "LAST":
            return [], None
        if max_results == ingest.FORWARD_LIMIT:
            return [], None
        return [], "LAST"

    monkeypatch.setattr(ingest, "get_channel_videos", fake_get_channel_videos)
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    state = {}
    ingest.ingest_brain("ada", config, state)
    assert state["ada"]["youtube"]["@ada"]["backfill_done"] is True


def test_ingest_brain_continues_when_handle_cannot_be_resolved(stub_pipeline, monkeypatch):
    ingest = stub_pipeline

    def boom(handle):
        raise ValueError("bad handle")

    monkeypatch.setattr(ingest, "resolve_yt_handle", boom)
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    state = {}
    assert ingest.ingest_brain("ada", config, state) == []
    assert state["ada"]["youtube"] == {}


def test_ingest_brain_substack_rss_source(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    posts = [
        {
            "url": "https://blog.test/p1",
            "title": "Post 1",
            "published": "2026-01-05T00:00:00Z",
            "text": " ".join(["word"] * 200),
        },
        {"url": "https://blog.test/empty", "title": "Empty", "published": "", "text": ""},
        {"url": "https://blog.test/short", "title": "Short", "published": "", "text": "too short"},
    ]
    monkeypatch.setattr(ingest, "get_substack_posts", lambda url, seen: posts)
    config = {
        **BRAIN_CONFIG,
        "sources": {"substack": [{"url": "https://blog.test", "name": "Blog"}]},
    }
    state = {}

    updated = ingest.ingest_brain("ada", config, state)

    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert "**Source:** Substack (Blog) |" in content
    assert "**Date:** 2026-01-05" in content
    assert content.count("\n## ") == 1
    assert updated == [str(ingest.BRAINS_DIR / "ada.md")]
    assert state["ada"]["substack"] == {
        "https://blog.test/p1": True,
        "https://blog.test/empty": True,
        "https://blog.test/short": True,
    }


def test_ingest_brain_substack_web_scrape_source_uses_batch_scraper(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    monkeypatch.setattr(
        ingest,
        "scrape_batch_newsletter",
        lambda url, seen: [
            {"url": "https://dl.ai/the-batch/1", "title": "Issue 1", "published": "", "text": " ".join(["w"] * 200)}
        ],
    )
    monkeypatch.setattr(ingest, "get_substack_posts", lambda url, seen: pytest.fail("wrong scraper"))
    config = {
        **BRAIN_CONFIG,
        "sources": {
            "substack": [{"url": "https://dl.ai/the-batch/", "name": "The Batch", "type": "web_scrape"}]
        },
    }
    state = {}
    ingest.ingest_brain("ada", config, state)
    assert "## Issue 1" in (ingest.BRAINS_DIR / "ada.md").read_text()


def test_ingest_brain_articles_skip_seen_and_unscrapable(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    texts = {
        "https://x.test/good": " ".join(["word"] * 200),
        "https://x.test/short": "tiny",
        "https://x.test/fail": None,
    }
    monkeypatch.setattr(ingest, "scrape_article", lambda url: texts[url])
    config = {
        **BRAIN_CONFIG,
        "sources": {
            "articles": [
                {"url": "https://x.test/good", "name": "Good"},
                {"url": "https://x.test/short"},
                {"url": "https://x.test/fail"},
                {"url": "https://x.test/seen"},
            ]
        },
    }
    state = {"ada": {
        "youtube": {}, "substack": {}, "articles": {"https://x.test/seen": True},
        "backfill_cursor": {}, "last_run": None,
    }}

    updated = ingest.ingest_brain("ada", config, state)

    content = (ingest.BRAINS_DIR / "ada.md").read_text()
    assert "## Good" in content
    assert content.count("\n## ") == 1
    assert updated == [str(ingest.BRAINS_DIR / "ada.md")]
    assert state["ada"]["articles"] == {"https://x.test/seen": True, "https://x.test/good": True}


def test_ingest_brain_dry_run_writes_no_files(stub_pipeline, monkeypatch):
    ingest = stub_pipeline
    monkeypatch.setattr(
        ingest, "get_channel_videos", lambda cid, max_results=50, page_token=None: ([_video("v1")], None)
    )
    config = {**BRAIN_CONFIG, "sources": {"youtube": [{"handle": "@ada"}]}}
    updated = ingest.ingest_brain("ada", config, {}, dry_run=True)
    assert updated == []
    assert list(ingest.BRAINS_DIR.iterdir()) == []


def test_ingest_brain_without_sources_is_noop(ingest):
    state = {}
    assert ingest.ingest_brain("ada", {**BRAIN_CONFIG}, state) == []
    assert state["ada"]["last_run"] is not None


# ── main ──────────────────────────────────────────────────────────────────────

def _write_config(ingest, slugs=("ada", "grace")):
    config = {"brains": {slug: {**BRAIN_CONFIG, "sources": {}} for slug in slugs}}
    ingest.CONFIG_FILE.write_text(ingest.yaml.safe_dump(config))


def test_main_runs_all_brains_and_saves_state(ingest, monkeypatch):
    _write_config(ingest)
    ran = []
    monkeypatch.setattr(ingest.sys, "argv", ["ingest.py"])
    monkeypatch.setattr(
        ingest,
        "ingest_brain",
        lambda slug, cfg, state, dry_run=False: ran.append(slug) or [f"/tmp/{slug}.md"],
    )
    committed = []
    monkeypatch.setattr(ingest, "commit_to_github", lambda files: committed.append(files))

    ingest.main()

    assert ran == ["ada", "grace"]
    assert ingest.STATE_FILE.exists()
    assert committed == [["/tmp/ada.md", "/tmp/grace.md"], [str(ingest.STATE_FILE)]]


def test_main_single_brain_filter(ingest, monkeypatch):
    _write_config(ingest)
    ran = []
    monkeypatch.setattr(ingest.sys, "argv", ["ingest.py", "--brain", "grace"])
    monkeypatch.setattr(
        ingest, "ingest_brain", lambda slug, cfg, state, dry_run=False: ran.append(slug) or []
    )
    monkeypatch.setattr(ingest, "commit_to_github", lambda files: pytest.fail("nothing to commit"))
    ingest.main()
    assert ran == ["grace"]


def test_main_unknown_brain_exits_nonzero(ingest, monkeypatch):
    _write_config(ingest)
    monkeypatch.setattr(ingest.sys, "argv", ["ingest.py", "--brain", "nobody"])
    with pytest.raises(SystemExit) as excinfo:
        ingest.main()
    assert excinfo.value.code == 1


def test_main_dry_run_skips_state_save_and_commit(ingest, monkeypatch):
    _write_config(ingest, slugs=("ada",))
    monkeypatch.setattr(ingest.sys, "argv", ["ingest.py", "--dry-run"])
    seen_dry_run = []
    monkeypatch.setattr(
        ingest,
        "ingest_brain",
        lambda slug, cfg, state, dry_run=False: seen_dry_run.append(dry_run) or [],
    )
    monkeypatch.setattr(ingest, "commit_to_github", lambda files: pytest.fail("dry run"))
    ingest.main()
    assert seen_dry_run == [True]
    assert not ingest.STATE_FILE.exists()


def test_main_logs_and_continues_when_a_brain_fails(ingest, monkeypatch, caplog):
    _write_config(ingest)

    def flaky(slug, cfg, state, dry_run=False):
        if slug == "ada":
            raise RuntimeError("kaboom")
        return []

    monkeypatch.setattr(ingest.sys, "argv", ["ingest.py"])
    monkeypatch.setattr(ingest, "ingest_brain", flaky)
    with caplog.at_level("ERROR"):
        ingest.main()
    assert "Failed to ingest brain 'ada'" in caplog.text
    assert json.loads(ingest.STATE_FILE.read_text()) == {}
