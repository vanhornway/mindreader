# Personal Brain System

A portable knowledge pipeline that ingests YouTube transcripts, Substack posts, and articles from specific people, then exposes them as a Claude.ai MCP connector so you can query their knowledge directly in chat.

## What it does

- **Daily ingestion** (GitHub Actions) — fetches new content automatically, whether your Mac is on or off
- **Backfill** — works backward through older content over time, 5 items per run
- **MCP server** (Railway) — exposes 3 tools to Claude.ai Pro:
  - `query_brain` — ask a specific person a question, answered in their style
  - `cross_query` — ask multiple brains the same question
  - `list_brains` — see what's available

---

## Setup — step by step

### 1. Fork / clone this repo

```bash
git clone https://github.com/yourusername/brain-system
cd brain-system
```

### 2. Get your API keys

| Key | Where to get it | Cost |
|-----|----------------|------|
| `YOUTUBE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → enable "YouTube Data API v3" | Free (10,000 units/day) |
| `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai) | ~$0.001–0.01 per video summarised |
| `MCP_AUTH_TOKEN` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Free |

### 3. Add secrets to GitHub

Go to your repo → Settings → Secrets and variables → Actions → New repository secret

Add these secrets:
- `YOUTUBE_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL` (optional, defaults to `google/gemini-flash-1.5`)

The `GITHUB_TOKEN` is provided automatically by GitHub Actions — no action needed.

### 4. Deploy the MCP server to Railway

1. Go to [railway.app](https://railway.app) and create a free account
2. New Project → Deploy from GitHub repo → select this repo
3. Railway will auto-detect the `Dockerfile`
4. In Railway → your service → Variables, add:
   - `OPENROUTER_API_KEY` — your key
   - `MCP_AUTH_TOKEN` — your generated token
   - `OPENROUTER_MODEL` — `anthropic/claude-3-5-haiku` (better for query responses)
5. In Railway → your service → Settings → Networking → Generate Domain
6. Copy the public URL (e.g. `https://brain-system-production.up.railway.app`)

> **Note on volumes:** Railway's free tier doesn't persist volumes between deploys.
> The brains/ directory is read from the GitHub repo directly. The MCP server
> fetches the latest brain files from GitHub on startup. See `mcp_server.py` for
> the `GITHUB_RAW_BASE` environment variable option if you want live file reading.

### 5. Register the MCP server in Claude.ai

1. Go to [claude.ai](https://claude.ai) → Settings → Connectors
2. Click **Add custom integration**
3. Name: `Personal Brains`
4. URL: `https://your-railway-url.up.railway.app`
5. Click Add — Claude will do the MCP handshake automatically
6. In a conversation, click the tools icon and enable "Personal Brains"

### 6. Create a Claude Project for your brains

1. In Claude.ai, create a new Project
2. Add this system prompt:

```
You have access to a set of personal knowledge brains via the "Personal Brains" MCP connector.

Available brains:
- nate_jones — Nate B. Jones, AI, future of careers and work
- dr_berg — Dr. Eric Berg, health, nutrition, fasting, metabolism
- andrew_ng — Andrew Ng, AI/ML, education, agentic AI

When the user asks to "ask Nate", "what would Berg say", "query Andrew Ng", or anything
that implies querying a specific brain, call the query_brain tool with the appropriate
brain slug and their question.

For comparative questions ("what do Nate and Andrew think about X"), use cross_query.

Always call list_brains first if the user asks what brains are available.
```

Now you can just say "Ask Nate what careers AI will kill first" and it works.

---

## Adding a new brain

1. Open `brains.yaml`
2. Add a new entry following the existing pattern (see the commented-out Amir Husain example)
3. Commit and push — GitHub Actions will start ingesting on the next scheduled run
4. Update the Claude Project system prompt to mention the new brain

That's it. No code changes needed.

## Adding a new source to an existing brain

Open `brains.yaml` and add a URL under the appropriate source type:

```yaml
nate_jones:
  sources:
    youtube:
      - handle: "@NateBJones"
        name: "Nate B Jones"
    articles:
      - url: "https://some-article.com/nate-wrote-this"  # ← add here
        name: "Article title"
```

Commit and push. The next ingestion run picks it up automatically.

---

## Running ingestion manually

```bash
# Run all brains
python ingestion/ingest.py

# Run one brain only
python ingestion/ingest.py --brain nate_jones

# See what would be fetched without writing anything
python ingestion/ingest.py --dry-run
```

You can also trigger the GitHub Action manually: Actions → Daily brain ingestion → Run workflow.

---

## File structure

```
brain-system/
├── brains.yaml              ← edit this to add/change sources
├── brains/
│   ├── nate_jones.md        ← auto-generated knowledge files
│   ├── dr_berg.md
│   └── andrew_ng.md
├── ingestion/
│   ├── ingest.py            ← ingestion script
│   ├── requirements.txt
│   └── state.json           ← tracks what's been ingested (auto-generated)
├── mcp_server/
│   ├── mcp_server.py        ← MCP server (FastAPI)
│   └── requirements.txt
├── .github/workflows/
│   └── daily-ingest.yml     ← GitHub Actions scheduler
├── Dockerfile               ← for Railway deployment
├── docker-compose.yml       ← for local / Pi deployment
└── .env.example             ← copy to .env and fill in
```

---

## Migrating to Raspberry Pi

When you want to run the MCP server locally instead of (or alongside) Railway:

```bash
# On your Pi
git clone https://github.com/yourusername/brain-system
cd brain-system
cp .env.example .env
# fill in .env values
docker-compose up -d
```

Expose it externally (so Claude.ai can reach it) using Cloudflare Tunnel — free, no port forwarding:

```bash
# Install cloudflared on Pi
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
# Then create a tunnel:
cloudflared tunnel --url http://localhost:8000
# Copy the generated *.trycloudflare.com URL into Claude.ai Connectors settings
```

The brain .md files are synced automatically — the ingestion script commits to GitHub,
and you can run `git pull` on the Pi via cron to keep them fresh:

```bash
# Add to Pi crontab (crontab -e):
0 7 * * * cd /home/pi/brain-system && git pull --quiet
```

---

## Cost estimate

At 10 new videos/day across 3 brains, using `google/gemini-flash-1.5`:
- Ingestion summarisation: ~$0.02/day
- MCP query responses: ~$0.01–0.05 per conversation
- YouTube API: free (well within 10k daily quota)
- GitHub Actions: free
- Railway: free tier (500 hours/month)

**Total: essentially free for personal use.**

---

## Personal Scripts

Documentation for personal automation scripts (health ingestion, AI tools, web apps, and more).

→ See [`scripts/`](scripts/README.md) for the full index.
