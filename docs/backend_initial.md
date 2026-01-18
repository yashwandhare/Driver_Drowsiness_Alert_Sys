---

# Backend Documentation

**AI-Based Driver Drowsiness Detection System**

---

## 1. Overview

This backend is responsible for **receiving video frames**, **processing them for driver drowsiness**, and **exposing the detection state** to external systems (ESP32 / alert modules).

Since physical hardware (ESP32-CAM) was not available during development, the backend is designed to work with a **browser-based camera simulation** that mimics an ESP32-CAM video stream.

The backend is built using:

* **FastAPI** for networking
* **WebSockets** for real-time video transport
* **OpenCV** for frame handling
* **MediaPipe Face Mesh** for facial landmark detection
* **Eye Aspect Ratio (EAR)** for drowsiness detection logic

---

## 2. High-Level Architecture

**Data Flow**

```
Browser Camera
   ↓ (base64 JPEG frames)
WebSocket (/ws/video)
   ↓
FastAPI Backend
   ↓
OpenCV Frame Decoder
   ↓
MediaPipe Face Mesh
   ↓
EAR Computation
   ↓
Drowsiness State Machine
   ↓
REST API (/status)
```

This design mirrors how an ESP32-CAM would stream MJPEG frames over Wi-Fi.

---

## 3. Backend Responsibilities

The backend performs four core tasks:

1. **Video Ingestion**

   * Receives base64-encoded JPEG frames via WebSocket
   * Decodes frames into OpenCV format

2. **Computer Vision Processing**

   * Detects facial landmarks using MediaPipe Face Mesh
   * Extracts eye landmarks
   * Computes Eye Aspect Ratio (EAR)

3. **Drowsiness Detection**

   * Uses temporal logic (not single-frame decisions)
   * Applies hysteresis to avoid flickering states
   * Classifies driver state as `NORMAL` or `DROWSY`

4. **State Exposure**

   * Exposes detection state via REST API
   * Designed for ESP32 or alert modules to poll periodically

---

## 4. File Structure (Backend)

```
backend/
├── app/
│   ├── main.py     # FastAPI app setup
│   ├── ws.py       # WebSocket + CV + drowsiness logic
│   └── config.py   # (optional / future use)
├── requirements.txt
└── README.md
```

---

## 5. File-by-File Explanation

---

## `main.py`

### Purpose

* Entry point of the FastAPI backend
* Registers routes and middleware

### What it does

* Creates the FastAPI app
* Enables CORS (browser access)
* Registers routes from `ws.py`
* Provides a health check endpoint

### Why it exists

Keeps application setup **separate from business logic**, making the system modular and maintainable.

---

## `ws.py`

This is the **core file** of the backend.

---

### 5.1 WebSocket Video Ingestion

```python
@router.websocket("/ws/video")
```

**Purpose**

* Receives live video frames from the browser (ESP32-CAM simulation)

**Why WebSocket**

* Low latency
* Persistent connection
* Suitable for continuous video streaming

**How it works**

* Browser sends base64-encoded JPEG frames
* Backend decodes frames using OpenCV
* Latest frame is pushed into a queue for processing

---

### 5.2 Frame Queue

```python
frame_queue = Queue(maxsize=1)
```

**Purpose**

* Ensures real-time processing
* Prevents memory buildup

**Why maxsize = 1**

* Old frames are dropped automatically
* System always processes the most recent frame
* Mimics real embedded system behavior

---

### 5.3 Computer Vision Worker Thread

```python
cv_worker()
```

**Purpose**

* Runs all heavy CV operations off the main thread

**Why a separate thread**

* FastAPI event loop stays responsive
* Video processing does not block networking
* Scales better for real-time systems

---

### 5.4 Face Detection (MediaPipe Face Mesh)

**Why MediaPipe**

* Lightweight
* Real-time capable
* Works well on low-power devices

**What is detected**

* 468 facial landmarks
* Eye landmarks used for EAR calculation

---

### 5.5 Eye Aspect Ratio (EAR)

**Definition**
EAR is a geometric measure of eye openness.

```
EAR = (vertical distances) / (horizontal distance)
```

**Why EAR**

* Simple
* No training required
* Widely used in academic and industrial systems

**Landmarks Used**

* Left eye: `[33, 160, 158, 133, 153, 144]`
* Right eye: `[362, 385, 387, 263, 373, 380]`

---

### 5.6 Drowsiness Detection Logic

Implemented as a **state machine**, not a single-frame check.

#### Key Features

* Temporal consistency (multiple frames required)
* Hysteresis (separate open/close thresholds)
* Thread-safe state handling

```python
EAR_CLOSE_THRESHOLD = 0.23
EAR_OPEN_THRESHOLD  = 0.26
DROWSY_FRAMES = 20
```

#### Why hysteresis

Prevents rapid flipping due to noise when EAR hovers near the threshold.

#### States

* `NORMAL`
* `DROWSY`

Eyes must remain closed for a **continuous duration** before triggering `DROWSY`.

---

### 5.7 Thread-Safe State Management

```python
class DrowsinessState:
```

**Why this exists**

* CV runs in a background thread
* API requests run in FastAPI threads
* Shared state must be synchronized

**What it stores**

* Current EAR value
* Number of closed frames
* Current drowsiness state

---

### 5.8 REST API – `/status`

```python
@router.get("/status")
```

**Purpose**

* Allows ESP32 or alert system to query driver state

**Returns**

```json
{
  "state": "DROWSY",
  "closed_frames": 20,
  "ear_current": 0.18
}
```

**Why REST instead of WebSocket**

* Simple polling
* ESP32-friendly
* Easy to debug and demo

---

## 6. Design Decisions (Why This Approach)

| Decision          | Reason                         |
| ----------------- | ------------------------------ |
| WebSocket video   | Low latency, continuous stream |
| Queue with size 1 | Prevents lag & memory leaks    |
| EAR instead of ML | Lightweight, explainable       |
| Hysteresis logic  | Prevents false triggers        |
| Threaded CV       | Keeps server responsive        |
| REST `/status`    | Easy ESP32 integration         |

---

## 7. Current Capabilities

✔ Real-time video ingestion
✔ Face detection
✔ Eye tracking
✔ Robust drowsiness detection
✔ ESP32-ready API
✔ Stable for long runtimes

---

## 8. Future Extensions

* ESP32 buzzer / vibration motor
* Sound alert on backend
* Night-time IR tuning
* Model-based eye state classifier (optional)

---
