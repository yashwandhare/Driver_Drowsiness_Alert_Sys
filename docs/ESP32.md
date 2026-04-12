# ESP32-CAM User Manual

## Hardware Used

- ESP32-CAM (AI Thinker, OV3660)
- Optional buzzer on GPIO13
- Built-in flash LED on GPIO4 (status + alert)
- USB-TTL adapter for flashing

## Wiring

### Flashing Connections (USB-TTL -> ESP32-CAM)

- `5V` -> `5V`
- `GND` -> `GND`
- `TX` -> `U0R` (GPIO3)
- `RX` -> `U0T` (GPIO1)
- `IO0` -> `GND` only while uploading

After upload:
- Disconnect `IO0` from `GND`
- Press `RST` once to boot normally

### Optional Buzzer Wiring

- Buzzer `+` -> `GPIO13`
- Buzzer `-` -> `GND`

For current testing from laptop USB, keep buzzer disconnected and use `ENABLE_BUZZER = false`.

## What Each Part Does

- Camera: captures JPEG frames and sends to backend.
- Flash LED: shows server + alert state.
- Buzzer (optional): audible alert only when state is `DROWSY`.

## Firmware File

Use:
- `esp32_cam/esp32_cam_stream/esp32_cam_stream.ino`

In Arduino IDE, update:
- `WIFI_SSID`
- `WIFI_PASSWORD`
- `SERVER_HOST` (laptop IP)
- `API_TOKEN` (only if backend token is enabled)
- `ENABLE_BUZZER` (`false` for current testing)

## Runtime Behavior

- On Wi-Fi + server success:
  - sends frames to `POST /frame`
  - polls `GET /status`
- LED states:
  - `OFF` disconnected
  - `SOLID ON` connected + normal
  - `FAST BLINK` connected + drowsy

## Stability Tips (Important)

- Keep frame size at VGA and JPEG quality around 12.
- Avoid adding heavy logic on ESP32.
- Keep upload/poll loops simple and non-blocking.
- Use a stable 5V supply when moving beyond laptop USB testing.

## About `webserver.ino`

`esp32_cam/webserver.ino` is a known working camera baseline and useful for hardware sanity checks (camera + Wi-Fi). Use it if you need to verify camera hardware before using full pipeline firmware.
