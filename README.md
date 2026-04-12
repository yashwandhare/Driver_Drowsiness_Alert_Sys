# Driver Drowsiness Alert System

Driver drowsiness detection using ESP32-CAM (OV3660) + FastAPI server.

## Core Flow

- ESP32-CAM captures JPEG frames and sends them to server (`POST /frame`).
- Server runs MediaPipe + EAR logic.
- ESP32 polls server state (`GET /status`) and controls LED/buzzer.

LED policy:
- OFF = disconnected
- SOLID ON = connected
- FAST BLINK = drowsy

## Docs

- `docs/PROJECT.md` complete step-by-step setup manual
- `docs/ESP32.md` hardware and firmware user manual
- `docs/SERVER.md` backend and detection documentation

## Quick Start

```bash
make setup-venv
make run
```

Open: `http://127.0.0.1:5500`

Frontend modes:
- `Debug` (default): no laptop webcam prompt, shows processed ESP32 stream + metrics.
- `Virtual`: click `Start Virtual Camera` to run laptop-only prototype.

## Important Files

- `esp32_cam/esp32_cam_stream/esp32_cam_stream.ino`
- `esp32_cam/webserver.ino` (camera hardware sanity-check firmware)
- `config/system_config.json`

## Backend Endpoints

- `GET /`
- `GET /status`
- `GET /config`
- `GET /metrics`
- `GET /debug/frame.jpg`
- `POST /frame`
- `WS /ws/video`

Optional auth:
- Set `network.api_token` in `config/system_config.json`
- Then send `X-API-Key` from clients (ESP32/frontend)
