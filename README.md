# Weather Central 🌦️

Automated national weather videos with a full-screen digital radar-station look. **The initial workflow is test-only:** it fetches official weather data, validates freshness, renders an MP4, and uploads the video as a GitHub Actions artifact. It does not upload to YouTube.

## What it does

- Uses the National Weather Service API for official alerts and forecast metadata.
- Downloads public NWS radar imagery for a radar-screen visual; radar frames are labeled with their observation/retrieval time and are not presented as forecasts.
- Renders a branded radar console with alert panel, ticker, timestamps, source attribution, and music-free captions.
- Fails closed on missing, stale, malformed, or contradictory critical data.
- Produces deterministic run IDs and a local manifest to help prevent duplicate future uploads.

## Run locally

Requires Python 3.11+ and FFmpeg.

```bash
python -m pip install -r requirements.txt
python weather_central.py --output output/weather-central.mp4
```

## GitHub Actions

The workflow runs daily and can also be started manually. It generates a test MP4 and publishes it as a downloadable workflow artifact. Test mode intentionally does not use YouTube credentials and never uploads a video publicly.

## Official sources

- NWS API: https://www.weather.gov/documentation/services-web-api
- NWS radar: https://radar.weather.gov/
- NOAA/NWS alert feeds: https://api.weather.gov/alerts/active

Use the latest official local instructions during hazardous weather. Weather Central is independent and is not affiliated with NOAA or the National Weather Service.

## Future YouTube publishing

Do not enable automatic publishing until the test artifact has been inspected and the workflow has been tested repeatedly. When adding YouTube OAuth, keep refresh tokens and client credentials in GitHub Actions Secrets, never in the repository. Persist uploaded video IDs/run fingerprints before retrying uploads to prevent duplicates. Re-check current NWS data immediately before any public upload.