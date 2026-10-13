# Weather Central 🌦️

Weather Central generates animated national weather-broadcast graphics from official National Weather Service data. The GitHub Actions workflow is **test mode only**: it creates an MP4 and a JSON manifest as a downloadable artifact. It never uploads to YouTube.

## Current test renderer

- Animated national NWS radar loop, preserved as supplied by the radar source.
- Active NWS alerts validated against their effective and expiration times.
- Alert cards sorted by severity, using official event names, headlines, and affected areas.
- An animated ticker built only from the active official alert feed.
- A separate geographic alert-footprint overview drawn from NWS GeoJSON polygons. This is intentionally **not overlaid on the radar**, because the two sources do not currently share a verified projection transform.
- Fetch timestamp, official source attribution, video dimensions, duration, alert counts, and validation metadata in `manifest.json`.
- MP4 checks using FFmpeg/ffprobe. Rendering fails if required source data is missing or the output is invalid.

The alert footprint overview shows only the continental United States and labels that Alaska, Hawaii, and territories are omitted from that inset. Official alerts for those places remain part of the alert cards/ticker when returned by the feed.

## Run locally

Requires Python 3.11+, FFmpeg, and network access to official NWS endpoints.

```bash
python -m pip install -r requirements.txt
python weather_central.py --output output/weather-central.mp4 --duration 30
```

Duration must be between 15 and 60 seconds. The default is 30 seconds at 12 frames per second, 1280×720.

## GitHub Actions

Open the **Actions** tab and run **Weather Central (test mode)** manually, or wait for the daily schedule. The successful run includes an artifact named `weather-central-test-…` containing the MP4 and `manifest.json`. Artifacts are retained for seven days.

## Official data sources

- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)
- [NWS active alerts feed](https://api.weather.gov/alerts/active)
- [NWS national radar](https://radar.weather.gov/)

Weather Central is an independent educational project and is not affiliated with NOAA or the National Weather Service. Always follow instructions from local emergency-management officials and the NWS during hazardous weather.

## Publishing status

**YouTube publishing is not implemented or enabled.** Do not enable automatic publishing until repeated test runs and human review confirm that the visuals, data, attribution, and video quality are appropriate. Future OAuth credentials must be stored in GitHub Actions Secrets, never in repository files. Future upload logic must persist upload IDs/fingerprints before retrying so failed retries cannot create duplicate public videos.
