# 05_ingest_libre3.py

**Location:** `health-ingestion/05_ingest_libre3.py`

## What It Does

Fetches continuous glucose monitor (CGM) readings from the Abbott Libre 3 via the unofficial LibreLinkUp API (the same API used by Nightscout and xDrip integrations). Upserts all readings into Supabase.

**Data fetched:**
- Current glucose reading (mg/dL) with trend arrow and trend message
- Historical graph readings from the last sensor window
- Device/connection metadata via the `patientId` from LibreLinkUp

> **Note:** Abbott does not offer a public API. This uses the same reverse-engineered endpoint that the CGM community (Nightscout, xDrip, Loop) has documented and widely adopted.

## Fallback Workflow

If the API is down or returns stale data, use the screenshot → OCR fallback:
1. Take a screenshot of the Libre app
2. Upload to ChatGPT with the prompt: *"Read the glucose values and give me a JSON list"*
3. Use `06_ingest_manual.py` or the Open Brain MCP to inject the values

## Usage

```bash
python 05_ingest_libre3.py
```

No date range parameter — fetches whatever the LibreLinkUp API returns for the active sensor window (typically the last few hours of readings).

## Environment Variables

```env
LIBRE_USERNAME=     # LibreView / LibreLinkUp email
LIBRE_PASSWORD=
SUPABASE_URL=
SUPABASE_KEY=
```

## Dependencies

```
requests
supabase
python-dotenv
```

## Supabase Table

`health_metrics` — upsert on `(source, metric_type, recorded_at)`

| Column | Value |
|--------|-------|
| `source` | `"libre3"` |
| `subject` | `"Umair"` |
| `metric_type` | `glucose` |
| `unit` | `mg/dL` |
| `metadata` | `{"trend": ..., "trend_message": ...}` |
