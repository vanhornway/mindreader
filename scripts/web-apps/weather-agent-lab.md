# Weather Agent Lab

**Location:** `weather-agent-Lab/`

## What It Does

A React/TypeScript weather dashboard web app with an animated, visually rich UI. Built as a lab/experiment for building weather-related agent features.

**Features:**
- Dynamic gradient background with animated abstract shapes
- `WeatherDashboard` component as the main UI surface
- Mobile-ready (includes iOS and Android Capacitor configs for native wrapping)
- Vite build tooling for fast development

## Stack

- React + TypeScript
- Vite
- Tailwind CSS
- Capacitor (iOS + Android native shell)

## Running Locally

```bash
cd weather-agent-Lab
npm install
npm run dev
# Visit http://localhost:5173
```

## Building

```bash
npm run build
# Output in dist/
```

## Mobile (Capacitor)

```bash
npx cap sync
npx cap open ios      # Open in Xcode
npx cap open android  # Open in Android Studio
```
