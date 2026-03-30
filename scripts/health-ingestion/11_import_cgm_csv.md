# 11_import_cgm_csv.py

**Location:** `health-ingestion/11_import_cgm_csv.py`

## What It Does

Batch-imports a LibreView CSV export into Supabase's `cgm_readings` table. Use this for backfilling historical glucose data when the live API wasn't running or for importing data from a previous sensor.

Handles LibreView's unusual CSV format which has a 2-row preamble before the actual column headers, and supports multiple timestamp formats.

**Parses:**
- Historic glucose readings (mg/dL)
- Scan glucose readings
- Strip (fingerstick) glucose readings
- Ketone values (mmol/L)
- Carbohydrates logged (grams)
- Rapid-acting insulin doses
- Long-acting insulin doses
- Notes
- Device serial number (from preamble)

Uploads in batches of 500 rows. Deduplicates on `(device_serial, recorded_at, record_type)`.

## Getting the CSV

1. Go to [LibreView.com](https://www.libreview.com)
2. My Reports → Export Data → CSV
3. Download the file

## Usage

```bash
python 11_import_cgm_csv.py path/to/libre_export.csv
```

Example output:
```
Parsing libre_export.csv…
  found 2847 readings  (device serial: 2N12345678)
  uploaded 500/2847 rows…
  uploaded 1000/2847 rows…
  ...
Done — 2847 rows upserted into cgm_readings
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

`cgm_readings` — upsert on `(device_serial, recorded_at, record_type)`

| Column | Description |
|--------|-------------|
| `recorded_at` | Timestamp from device |
| `record_type` | LibreView record type integer (0=historic, 1=scan, etc.) |
| `glucose_mg_dl` | Historic glucose reading |
| `scan_glucose` | Manual scan reading |
| `strip_glucose` | Fingerstick reading |
| `ketone_mmol` | Ketone level |
| `carbs_grams` | Logged carbohydrates |
| `rapid_insulin` | Rapid-acting insulin dose |
| `long_insulin` | Long-acting insulin dose |
| `notes` | User notes |
| `source` | `"csv"` |
| `device_serial` | From CSV preamble |
| `subject` | `"Umair"` |
