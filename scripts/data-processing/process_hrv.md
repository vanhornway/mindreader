# process_hrv.py

**Location:** `Downloads/process_hrv.py`

## What It Does

Processes Fitbit HRV (Heart Rate Variability) and respiratory rate data exported from Google Takeout. Merges multiple export archives, deduplicates, and produces a year-over-year pivot table in CSV format for trend analysis.

**Input:** Google Takeout exports from Fitbit
- `Takeout/Fitbit/Heart Rate Variability/` — CSV files with daily HRV metrics
- Respiratory rate data alongside HRV

**Output:** A pivoted CSV with dates as rows and years as columns, making it easy to compare the same time of year across multiple years.

## How to Get the Data

1. Go to [Google Takeout](https://takeout.google.com)
2. Select only **Fitbit**
3. Download and extract the archive(s)
4. Place the extracted folder(s) in `~/Downloads/Takeout/`

## Usage

```bash
cd ~/Downloads
python process_hrv.py
```

Output CSV is written to the current directory.

## Dependencies

```
pandas
```

Install:
```bash
pip install pandas
```
