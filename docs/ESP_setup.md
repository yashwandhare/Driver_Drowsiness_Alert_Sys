---

# ESP32 Setup Documentation

## Overview

The ESP32 module captures video frames using the OV2640 camera and streams them to a laptop backend for drowsiness detection.
The ESP32 does not perform any computer vision or detection logic. It only handles video capture, communication, and alert outputs.

---

## Hardware Components

* ESP32-CAM module
* OV2640 camera
* Green LED (connection indicator)
* Red LED (drowsiness alert indicator)
* Buzzer (audible alert)
* Resistors as required
* External power supply (recommended)

---

## Functional Responsibilities

### ESP32

* Capture frames from OV2640
* Send frames to backend over Wi-Fi
* Poll backend for drowsiness status
* Control LEDs and buzzer based on backend response

### Backend

* Receive video frames
* Perform face and eye analysis
* Detect drowsiness
* Expose detection state via API

---

## Connection & Streaming Logic

### Green LED (Connection Indicator)

Purpose: Visual confirmation that the ESP32 is connected and streaming.

Behavior:

* OFF → Not connected to backend
* ON (solid) → Backend reachable and frames are being sent successfully

---

## Alert Logic

The ESP32 polls the backend periodically using:

```
GET /status
```

Expected response:

```json
{
  "state": "NORMAL" | "DROWSY"
}
```

---

## Red LED Behavior (Visual Alert)

Purpose: Debugging and visual indication of alert events.

Behavior:

* First drowsiness detection

  * Red LED blinks once
  * Then stays ON
* Each subsequent drowsiness detection

  * Red LED blinks again
  * Then stays ON
* When driver returns to normal state

  * Red LED turns OFF
  * Alert counter resets

---

## Buzzer Behavior (Audible Alert)

Purpose: Warn the driver during drowsiness.

Behavior:

* `DROWSY` → Buzzer ON (continuous)
* `NORMAL` → Buzzer OFF

The buzzer remains active for the entire duration the driver is detected as drowsy.

---

## ESP32 Control Logic (Conceptual)

```
if state == DROWSY:
    buzzer ON
    if previous_state == NORMAL:
        blink red LED
        red LED ON
else:
    buzzer OFF
    red LED OFF
```

Green LED is controlled independently based on connection status.

---

## Design Notes

* ESP32 performs no image processing
* All detection logic is centralized in the backend
* Polling is used for simplicity and reliability
* LEDs provide clear debugging and demo feedback
* Design is compatible with real-time embedded constraints

---

## Future Extensions

* Replace polling with push notifications
* Add vibration motor instead of buzzer
* Log alert events locally on ESP32
* Power optimization for vehicle use

---
