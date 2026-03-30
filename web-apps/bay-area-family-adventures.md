# Bay Area Family Adventures

**Location:** `bay-area-family-adventures/`

## What It Does

An interactive web map for discovering family-friendly hiking trails in the Bay Area. Built with Leaflet.js and OpenStreetMap tiles.

**Features:**
- Interactive map with marker clustering (markers group together at low zoom)
- Filter trails by max distance (miles) and max elevation gain (feet)
- Text search by trail name
- Each trail marker shows:
  - Kid-friendliness rating (out of 3)
  - Distance and elevation
  - Nearby coffee options
  - Nearby halal food options
  - Direct link to AllTrails

## Stack

- [Leaflet.js](https://leafletjs.com/) — interactive maps
- [Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) — marker clustering
- OpenStreetMap tiles — map tiles
- `trails.json` — local trail data file

## Running Locally

```bash
cd bay-area-family-adventures
# Open index.html in a browser
# OR serve it (required for fetch to work):
python -m http.server 8080
# Visit http://localhost:8080
```

## Trail Data Format (`trails.json`)

```json
[
  {
    "name": "Trail Name",
    "lat": 37.5,
    "lon": -122.1,
    "distance": 3.5,
    "elevation": 400,
    "kid": 3,
    "coffee": "Blue Bottle nearby",
    "halal": "Zaytoon 0.5mi away",
    "alltrails": "https://www.alltrails.com/trail/..."
  }
]
```
