# Umair's Personal Scripts

A collection of personal automation and data pipeline scripts spanning health tracking, AI tools, and family utilities.

## Overview

| Category | Scripts | Description |
|----------|---------|-------------|
| [Health Ingestion](#health-ingestion) | 6 scripts | Sync wearables & CGM data → Supabase |
| [Data Processing](#data-processing) | 1 script | Analyze Fitbit HRV/respiratory trends |
| [AI Tools](#ai-tools) | 1 script | Telegram bot bridging to Agent Zero |
| [Web Apps](#web-apps) | 2 apps | Bay Area trail finder, weather dashboard |
| [Hike Pipeline](#hike-pipeline) | 10 modules | Detect & classify hiking attendance from health data |

---

## Health Ingestion

All scripts live in `health-ingestion/` and write to a Supabase `health_metrics` table. Shared setup:

```bash
pip install requests supabase python-dotenv
cp .env.example .env  # fill in credentials
```

| Script | Docs | Purpose |
|--------|------|---------|
| `03_ingest_whoop.py` | [→](health-ingestion/03_ingest_whoop.md) | Whoop API: recovery, HRV, sleep, strain |
| `04_ingest_fitbit.py` | [→](health-ingestion/04_ingest_fitbit.md) | Fitbit API: steps, sleep, VO2 max, body comp |
| `05_ingest_libre3.py` | [→](health-ingestion/05_ingest_libre3.md) | Libre 3 CGM: continuous glucose readings |
| `06_ingest_manual.py` | [→](health-ingestion/06_ingest_manual.md) | CLI entry: blood pressure, Lumen, Mendi, weight |
| `07_run_all.py` | [→](health-ingestion/07_run_all.md) | Orchestrator: run all ingestors sequentially |
| `11_import_cgm_csv.py` | [→](health-ingestion/11_import_cgm_csv.md) | Batch import LibreView CSV exports |

---

## Data Processing

| Script | Docs | Purpose |
|--------|------|---------|
| `process_hrv.py` | [→](data-processing/process_hrv.md) | Merge Fitbit HRV/respiratory Google Takeout exports into year-over-year CSV |

---

## AI Tools

| Script | Docs | Purpose |
|--------|------|---------|
| `bot.py` | [→](ai-tools/bot.md) | Telegram ↔ Agent Zero bridge with project and context support |

---

## Web Apps

| App | Docs | Purpose |
|-----|------|---------|
| `bay-area-family-adventures/` | [→](web-apps/bay-area-family-adventures.md) | Interactive Leaflet map of Bay Area hiking trails with kid ratings and halal food nearby |
| `weather-agent-Lab/` | [→](web-apps/weather-agent-lab.md) | React/TypeScript weather dashboard with animated UI |

---

## Hike Pipeline

`my-open-brain/scripts/record-hike/` — multi-phase pipeline that detects hiking attendance from health signals.

Docs: [→](hike-pipeline/record-hike.md)

---

## Environment Variables Summary

| Variable | Used By |
|----------|---------|
| `WHOOP_CLIENT_ID/SECRET/USERNAME/PASSWORD` | 03_ingest_whoop |
| `FITBIT_ACCESS_TOKEN/REFRESH_TOKEN/CLIENT_ID/SECRET` | 04_ingest_fitbit |
| `LIBRE_USERNAME/PASSWORD` | 05_ingest_libre3 |
| `SUPABASE_URL/KEY` | all health-ingestion scripts |
| `SUPABASE_SERVICE_ROLE_KEY` | record-hike pipeline |
