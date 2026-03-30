# Record Hike — Season Evaluation Pipeline

**Location:** `my-open-brain/scripts/record-hike/`

## What It Does

A multi-phase pipeline that automatically detects whether a hiking attendance record should be logged, based on behavioral health signals (steps, activity strain) and Strava GPS confirmation. Designed to evaluate an entire hiking season's attendance history.

## Architecture

```
run_evaluation.py              ← main entry point
├── Phase 0: Load data         (src/loaders.py)
├── Phase 1: Behavior detect   (src/phase1_behavior.py)
├── Phase 2: Strava confirm    (src/phase2_confirmation.py)
├── Phase 3: Classify          (src/classifier.py)
└── Output: Report             (src/reporting.py)

Supporting modules:
  src/models.py        — data models (HikeRecord, HealthMetricRecord, etc.)
  src/baselines.py     — compute behavioral baselines for detection thresholds
  src/evaluate.py      — compare detected attendance vs ground truth
  src/audit.py         — data quality checks
  src/repair.py        — data repair utilities
```

## How It Works

**Phase 0 — Data Loading**
Fetches hiking history, daily health metrics, and Strava activities from Supabase.

**Phase 1 — Behavioral Detection**
Analyzes daily health aggregates (step count, Whoop strain score) against computed baselines to flag days that look like hikes (high steps, elevated strain).

**Phase 2 — Strava Confirmation**
For behaviorally-flagged days, queries the Strava API to see if a matching hiking or outdoor activity was recorded. Adds a confirmation signal.

**Phase 3 — Classification**
Combines behavioral and Strava signals into a final attendance decision (attended / did not attend / uncertain).

**Reporting**
Outputs a rich-formatted console report (via the `rich` library) showing the full season with per-date classifications and confidence levels.

## Usage

```bash
cd my-open-brain/scripts/record-hike
pip install -r requirements.txt
python run_evaluation.py
```

## Environment Variables

```env
SUPABASE_SERVICE_ROLE_KEY=    # needs service role (not anon key) for full read access
```

Strava tokens are read from a local JSON file (configured in `config/`).

## Dependencies

```
supabase
requests
rich
python-dotenv
```

## Supabase Tables Used

| Table | Purpose |
|-------|---------|
| `hiking_history` | Ground truth hike records |
| `health_metrics` | Daily steps, strain from Whoop/Fitbit |
| `strava_activities` | GPS-confirmed outdoor activities |
