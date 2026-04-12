# Server Documentation

## Stack

- FastAPI + Uvicorn
- OpenCV
- MediaPipe Face Mesh
- NumPy

## Server Responsibilities

- Receive camera frames from browser (`/ws/video`) or ESP32 (`/frame`).
- Run EAR-based drowsiness detection.
- Expose current detection state via `/status`.
- Expose runtime config via `/config`.

## Detection Logic (Concise)

1. Decode latest JPEG frame.
2. Detect face mesh landmarks.
3. Compute Eye Aspect Ratio (EAR) from eye landmarks.
4. Run state machine with hysteresis:
   - EAR < close threshold for N frames -> `DROWSY`
   - EAR > open threshold -> `NORMAL`

Config values are read from `config/system_config.json`.

## API Endpoints

- `GET /` health
- `GET /status` current state JSON
- `GET /config` active config JSON
- `GET /metrics` lightweight frame counters
- `GET /debug/frame.jpg` latest processed frame (mesh + EAR/state overlay)
- `POST /frame` raw JPEG bytes from ESP32
- `WS /ws/video` browser simulation stream

If `network.api_token` is set in `config/system_config.json`, send it in header:
- `X-API-Key: <token>`

## How to Run

1. Install dependencies:
   - `make setup-venv`
2. Start backend + frontend:
   - `make run`
3. Frontend URL:
   - `http://127.0.0.1:5500`

Frontend modes:
- `Virtual`: requests laptop webcam only after you click `Start Virtual Camera`.
- `Debug`: no webcam prompt; pulls processed ESP32 frames and backend telemetry.

## Config File

`config/system_config.json` includes:
- `camera` model and stream defaults
- `network` ports and polling intervals
- `detection` thresholds
- `alerts` GPIO and LED behavior settings

## Replication Notes

- Keep queue size at 1 frame to avoid lag buildup.
- Keep CV in worker thread to avoid blocking API loop.
- Use browser mode first; then switch to ESP32 hardware mode.
- Keep `detection.show_display=false` to avoid OpenCV desktop window freezes; use frontend Debug preview instead.
