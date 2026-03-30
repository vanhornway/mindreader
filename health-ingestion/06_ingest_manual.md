# 06_ingest_manual.py

**Location:** `health-ingestion/06_ingest_manual.py`

## What It Does

CLI tool for manually entering health readings that don't have an automated API integration. Inserts a timestamped record into Supabase immediately at the time of invocation.

**Supports:**
- Blood pressure (systolic, diastolic, pulse)
- Lumen metabolic score (1–5 scale)
- Mendi brain training score
- Weight + optional body fat % + optional muscle mass

## Usage

```bash
# Blood pressure: systolic diastolic pulse
python 06_ingest_manual.py bp 118 76 68

# Lumen metabolic score (1-5)
python 06_ingest_manual.py lumen 3

# Mendi brain training score
python 06_ingest_manual.py mendi 72

# Weight only
python 06_ingest_manual.py weight 216.5

# Weight + body fat %
python 06_ingest_manual.py weight 216.5 18.2

# Weight + body fat % + muscle mass
python 06_ingest_manual.py weight 216.5 18.2 162.4
```

## Environment Variables

```env
SUPABASE_URL=
SUPABASE_KEY=
```

## Dependencies

```
supabase
python-dotenv
```

## Supabase Table

`health_metrics` — inserts (not upserted, uses current timestamp)

| Command | `source` | `metric_type` | `unit` |
|---------|----------|---------------|--------|
| `bp` | `bp_machine` | `systolic`, `diastolic`, `pulse` | `mmHg`, `bpm` |
| `lumen` | `lumen` | `metabolic_score` | `level` |
| `mendi` | `mendi` | `brain_score` | `score` |
| `weight` | `withings` | `weight`, `body_fat_pct`, `muscle_mass_lbs` | `lbs`, `%` |
