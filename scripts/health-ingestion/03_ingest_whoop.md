# 03_ingest_whoop.py

**Location:** `health-ingestion/03_ingest_whoop.py`

## What It Does

Authenticates with the Whoop API using OAuth2 password grant and pulls the last N days of health data into Supabase's `health_metrics` table.

**Metrics fetched:**
- Recovery score (%)
- HRV — RMSSD in milliseconds
- Resting heart rate (bpm)
- SpO2 (%)
- Respiratory rate (rpm)
- Sleep: total hours, REM hours, deep hours, sleep performance score
- Strain score per workout

All records are upserted with deduplication on `(source, metric_type, recorded_at)`.

## Usage

```bash
python 03_ingest_whoop.py           # last 7 days (default)
```

To change the window, edit `ingest(days_back=7)` at the bottom or import and call directly:

```python
from 03_ingest_whoop import ingest
ingest(days_back=30)
```

## Environment Variables

```env
WHOOP_CLIENT_ID=
WHOOP_CLIENT_SECRET=
WHOOP_USERNAME=
WHOOP_PASSWORD=
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
| `source` | `"whoop"` |
| `subject` | `"Umair"` |
| `metric_type` | `recovery_score`, `hrv`, `resting_heart_rate`, `spo2`, `sleep_hours`, `sleep_rem_hours`, `sleep_deep_hours`, `sleep_score`, `strain` |
