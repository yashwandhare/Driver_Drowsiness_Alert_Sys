# Driver Drowsiness Alert System (OV3660 + ESP32-CAM)

## What This Project Does

- ESP32-CAM captures JPEG frames (10-12 FPS target).
- Laptop server runs MediaPipe landmark-based drowsiness detection.
- ESP32 polls server state and drives alerts:
  - Flash LED OFF when server disconnected
  - Flash LED solid ON when connected
  - Flash LED fast blink when `DROWSY`
  - Buzzer on GPIO13 (optional during testing)

## Architecture

```text
ESP32-CAM (OV3660) --POST /frame--> FastAPI server
ESP32-CAM <--GET /status-- FastAPI server
```

## Repository Layout

- `esp32_cam/esp32_cam_stream/esp32_cam_stream.ino` ESP32 Arduino IDE firmware
- `backend/` FastAPI + OpenCV + MediaPipe backend
- `frontend/index.html` browser debug panel
- `config/system_config.json` editable runtime config

## Step-by-Step Manual (Clone to Detection)

1. Clone repo and open folder.
2. Create Python environment and install packages:
   - `make setup-venv`
3. Start server + frontend:
   - `make run`
4. Open frontend at `http://127.0.0.1:5500`.
5. In Arduino IDE:
   - Open `esp32_cam/esp32_cam_stream/esp32_cam_stream.ino`
   - Select board: `AI Thinker ESP32-CAM`
   - Enter Wi-Fi SSID/password and laptop IP
   - If using API token, set same token in firmware `API_TOKEN` and backend config
   - Keep `ENABLE_BUZZER = false` for laptop USB testing
   - Upload firmware
6. Power ESP32-CAM and open Serial Monitor at `115200`.
7. Confirm logs show Wi-Fi connected and frame/status requests working.
8. Verify backend `/status` changes and LED behavior:
   - connected normal: solid ON
   - drowsy: fast blink
9. After stable test, optionally set `ENABLE_BUZZER = true` and connect buzzer physically.

## Prerequisites

- Python 3.11+
- Arduino IDE 2.x
- ESP32 board package installed in Arduino IDE
- ESP32-CAM (OV3660), USB-TTL programmer, jumper wires
- Same Wi-Fi network for laptop and ESP32

## Notes

- Backend is the only place doing detection.
- ESP32 remains lightweight to reduce resets/crashes.
- Tune detection and camera params in `config/system_config.json`.
