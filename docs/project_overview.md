---

# Driver Drowsiness Alert System

🚗 **AI-Based Driver Drowsiness Detection and Alert System**

---

## Aim

To design and implement a vision-based driver drowsiness alert system that detects prolonged eye closure using facial landmarks and triggers real-time alerts to prevent fatigue-related accidents.

---

## Objectives

* Capture real-time facial video using an ESP32-CAM
* Detect driver eye state using computer vision techniques
* Identify drowsiness based on sustained eye closure
* Trigger alerts using buzzer and LED through embedded hardware
* Demonstrate a scalable architecture suitable for in-vehicle deployment

---

## Problem Statement

* Driver fatigue is a major cause of road accidents
* Existing systems rely on steering or speed data, which respond late
* Vision-based monitoring enables early detection using physiological cues
* A low-cost, embedded, real-time alerting solution is required

---

## Proposed Solution

The system uses an ESP32-CAM to capture live video of the driver’s face.
Video frames are transmitted to an edge compute unit (laptop for demonstration, Raspberry Pi for deployment), where eye closure is detected using facial landmarks.

When drowsiness is detected, an alert signal is sent back to the ESP32-CAM to activate a buzzer and visual indicators.

---

## System Architecture

### Logical Flow

```
ESP32-CAM → Wi-Fi → Edge Compute (Laptop / Raspberry Pi)
Edge Compute → Decision → ESP32-CAM → Alert
```

### Component Roles

* **ESP32-CAM**: Camera input, Wi-Fi communication, alert actuation
* **FastAPI Server**: Communication and control layer
* **Vision Module**: Eye detection and drowsiness logic

---

## Hardware Components

* ESP32-CAM (AI-Thinker with OV2640 camera)
* Active buzzer (5V)
* LED with 220Ω resistor
* Mini breadboard (~170 tie points)
* Jumper wires (male-to-male)
* Power source (USB or power bank)

---

## Software and Technology Stack

### Embedded Side

* Arduino framework (ESP32-CAM firmware)
* MJPEG video streaming over Wi-Fi
* GPIO control for LED and buzzer

### Backend / Vision Side

* Python 3
* OpenCV
* MediaPipe (facial landmark detection)
* FastAPI with Uvicorn

---

## Why MediaPipe?

* Lightweight and efficient
* Suitable for real-time processing
* No model training required
* Stable performance on edge devices

---

## Image Acquisition Workflow

1. ESP32-CAM captures video frames
2. Frames are streamed over Wi-Fi
3. FastAPI backend receives the frames
4. Frames are passed to the vision module

The ESP32-CAM performs **no image processing** and acts only as a capture and transmission device.

---

## Drowsiness Detection Logic

### Step-by-Step Process

1. Face detected in video frame
2. Facial landmarks extracted
3. Eye landmarks isolated
4. Eye Aspect Ratio (EAR) computed
5. EAR compared against predefined thresholds
6. EAR below threshold for consecutive frames indicates drowsiness

---

## Why Eye Aspect Ratio (EAR)?

* Normal blinking is brief
* Drowsiness causes prolonged eye closure
* Temporal analysis reduces false detections

---

## Decision and Alert Workflow

* Vision module classifies state as:

  * `NORMAL`
  * `DROWSY`
* Backend exposes detection result via `/status` endpoint
* ESP32-CAM polls `/status` periodically

### Alert Behavior

* **DROWSY**: Buzzer ON, LED ON
* **NORMAL**: Alerts OFF

---

## Power Workflow

### Demonstration Setup

* ESP32-CAM powered via laptop USB or power bank

### Deployment Scenario

* Vehicle 12V battery
* Buck converter to 5V
* Powers Raspberry Pi and ESP32-CAM

---

## Applications

* Commercial truck and bus monitoring
* Taxi and logistics fleets
* Automotive safety systems (ADAS)
* Long-distance and night driving

---

## Future Scope

* Replace laptop with Raspberry Pi
* Add vibration-based alert mechanisms
* Integrate vehicle CAN bus data
* Improve robustness using infrared camera

---

## Conclusion

The proposed system demonstrates a low-cost, real-time driver drowsiness detection approach using edge-based computer vision and embedded alerts. The architecture is scalable, explainable, and suitable for in-vehicle deployment.

---
