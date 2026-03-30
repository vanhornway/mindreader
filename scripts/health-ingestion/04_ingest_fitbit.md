# 04_ingest_fitbit.py

**Location:** `health-ingestion/04_ingest_fitbit.py`

## What It Does

Pulls health data from the Fitbit API for the last N days and upserts it into Supabase. Uses OAuth2 with automatic token refresh when the access token expires (every ~8 hours).

**Metrics fetched:**
- Steps (per day)
- Calories burned (per day)
- Resting heart rate (per day)
- Sleep: total hours, REM hours, deep hours
- Body weight (lbs, converted from kg)
- Body fat percentage
- VO2 max (cardio fitness score — midpoint of reported range)

## Usage

```bash
python 04_ingest_fitbit.py          # last 7 days (default)
```

When the access token expires, the script auto-refreshes and prints the new token to stdout — copy it back to your `.env`.

## Environment Variables

```env
FITBIT_ACCESS_TOKEN=       # expires ~8hrs, refresh as needed
FITBIT_REFRESH_TOKEN=
FITBIT_CLIENT_ID=
FITBIT_CLIENT_SECRET=
SUPABASE_URL=
SUPABASE_KEY=
```

**Getting tokens:** Register an app at https://dev.fitbit.com/apps, use the OAuth2 Authorization Code flow to get initial tokens.

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
| `source` | `"fitbit"` |
| `subject` | `"Umair"` |
| `metric_type` | `steps`, `calories_burned`, `resting_heart_rate`, `sleep_hours`, `sleep_rem_hours`, `sleep_deep_hours`, `weight`, `body_fat_pct`, `vo2max` |
