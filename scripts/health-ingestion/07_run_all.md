# 07_run_all.py

**Location:** `health-ingestion/07_run_all.py`

## What It Does

Orchestrator script that runs all three API ingestors (Whoop, Fitbit, Libre 3) sequentially in a single command. Errors from any individual script are caught and printed without stopping the rest of the pipeline.

**Runs in order:**
1. `03_ingest_whoop.py`
2. `04_ingest_fitbit.py`
3. `05_ingest_libre3.py`

## Usage

```bash
python 07_run_all.py          # sync last 7 days (default)
python 07_run_all.py 30       # sync last 30 days
```

The `days_back` argument is passed to Whoop and Fitbit. Libre 3 always fetches the current sensor window regardless.

## Scheduling

To run daily, add to cron:

```bash
# Run every morning at 6am
0 6 * * * cd /Users/mumair/health-ingestion && python 07_run_all.py >> /tmp/health_ingest.log 2>&1
```

## Dependencies

Inherits all dependencies from the individual ingestor scripts.
