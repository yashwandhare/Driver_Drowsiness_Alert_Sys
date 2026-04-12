"""
Driver Drowsiness Alert System — Backend

Receives JPEG frames from the ESP32 or browser, runs MediaPipe EAR-based
drowsiness detection in a background thread, and serves results over REST.

The /frame endpoint returns the current drowsy state in the X-Drowsy-State
response header so the ESP32 does not need a separate /status poll.
"""

import base64
import logging
import math
import threading
import time
from queue import Empty, Queue
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np
from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.config import SETTINGS

logger = logging.getLogger("das")

# ── Detection config ──────────────────────────────────────────────────────────
EAR_CLOSE     = SETTINGS["detection"]["ear_close_threshold"]   # eyes closing
EAR_OPEN      = SETTINGS["detection"]["ear_open_threshold"]    # eyes clearly open
DROWSY_FRAMES = SETTINGS["detection"]["drowsy_frames"]         # consecutive low-EAR frames
SHOW_DISPLAY  = SETTINGS["detection"]["show_display"]
API_TOKEN     = SETTINGS["network"].get("api_token", "").strip()

MAX_BODY_BYTES = 10 * 1024 * 1024   # 10 MB hard cap
MP_SIZE        = 160                 # MediaPipe input resolution (square)
EAR_EMA_ALPHA  = 0.55               # EMA weight for newest sample (higher = more responsive)
STALE_RESET_S  = 8                  # reset state when no frame arrives for this many seconds
DEBUG_EVERY    = 2                  # render debug JPEG every N frames (reduce CPU)
DEBUG_QUALITY  = 60                 # JPEG quality for debug frame output

# MediaPipe eye landmark indices (6-point EAR).
LEFT_EYE  = [33,  160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

mp_face_mesh = mp.solutions.face_mesh


# ── Drowsiness state machine ──────────────────────────────────────────────────

class DrowsinessState:
    """Thread-safe EAR-based state machine: NORMAL ↔ DROWSY."""

    def __init__(self):
        self._lock         = threading.Lock()
        self.state         = "NORMAL"
        self.closed_frames = 0
        self.ear_raw       = 0.0
        self.ear_ema       = None
        self.alert_active  = False
        self.no_face       = 0        # consecutive frames with no face

    def update(self, ear: Optional[float]) -> None:
        with self._lock:
            if ear is None:
                # A few missed face detections (blink, head turn) are expected.
                # Only reset after several consecutive misses to avoid flickering.
                self.no_face += 1
                if self.no_face >= 4:
                    self._reset_unlocked()
                return

            self.no_face = 0
            self.ear_raw = ear
            self.ear_ema = (EAR_EMA_ALPHA * ear + (1 - EAR_EMA_ALPHA) * self.ear_ema
                            if self.ear_ema is not None else ear)

            if ear < EAR_CLOSE:
                self.closed_frames += 1
                if self.closed_frames >= DROWSY_FRAMES:
                    if self.state != "DROWSY":
                        logger.warning("ALERT drowsy  ear=%.3f  cf=%d", ear, self.closed_frames)
                    self.state = "DROWSY"
                    self.alert_active = True
            elif ear > EAR_OPEN:
                self._reset_unlocked()
            else:
                # Mid-band: decay closed counter slowly so recovery isn't instant.
                self.closed_frames = max(0, self.closed_frames - 1)
                if self.closed_frames == 0 and self.state == "DROWSY":
                    self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self.state         = "NORMAL"
        self.closed_frames = 0
        self.alert_active  = False
        self.ear_ema       = None

    def stale_reset(self) -> None:
        with self._lock:
            self._reset_unlocked()
            self.no_face = 0
        logger.info("[CV] stale reset")

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":         self.state,
                "closed_frames": self.closed_frames,
                "ear_current":   round(self.ear_raw, 3),
                "ear_ema":       round(self.ear_ema, 3) if self.ear_ema is not None else None,
                "alert_active":  self.alert_active,
            }


# ── Stream statistics ─────────────────────────────────────────────────────────

class StreamStats:
    def __init__(self):
        self._lock   = threading.Lock()
        self.rx      = 0
        self.fail    = 0
        self.enq     = 0
        self.last_ms = 0
        self._recent: list = []

    def on_rx(self) -> None:
        with self._lock:
            self.rx     += 1
            self.last_ms = int(time.time() * 1000)

    def on_decode(self, ok: bool) -> None:
        with self._lock:
            if not ok: self.fail += 1
            self._recent.append(ok)
            if len(self._recent) > 20:
                self._recent.pop(0)

    def on_enq(self) -> None:
        with self._lock: self.enq += 1

    def fail_rate(self) -> float:
        with self._lock:
            return self._recent.count(False) / len(self._recent) if self._recent else 0.0

    def age_ms(self) -> int:
        with self._lock:
            return (int(time.time() * 1000) - self.last_ms) if self.last_ms else 0

    def snapshot(self) -> dict:
        with self._lock:
            recent = list(self._recent)
            last_ms = self.last_ms
            rx = self.rx
            fail = self.fail
            enq = self.enq

        fail_rate = (recent.count(False) / len(recent)) if recent else 0.0
        age_ms = (int(time.time() * 1000) - last_ms) if last_ms else 0

        return {
            "frame_rx_total": rx,
            "frame_decode_fail_total": fail,
            "frame_enqueue_total": enq,
            "last_frame_ms": last_ms,
            "decode_fail_rate": round(fail_rate, 3),
            "last_frame_age_ms": age_ms,
        }


# ── Debug frame store ─────────────────────────────────────────────────────────

class DebugStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None

    def put(self, data: bytes) -> None:
        with self._lock: self._jpeg = data

    def get(self) -> Optional[bytes]:
        with self._lock: return self._jpeg


# ── Globals ───────────────────────────────────────────────────────────────────

drowsiness   = DrowsinessState()
stats        = StreamStats()
debug_store  = DebugStore()
frame_queue  = Queue(maxsize=1)
_stop        = threading.Event()
_cv_thread: Optional[threading.Thread] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def require_api_key(key: Optional[str]) -> None:
    if API_TOKEN and key != API_TOKEN:
        raise HTTPException(401, "Invalid API key")


def decode_jpeg(data: bytes) -> Optional[np.ndarray]:
    if not data: return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def enqueue(img: Optional[np.ndarray]) -> None:
    stats.on_decode(img is not None)
    if img is None: return
    # Drop oldest frame to avoid queue lag — always process the freshest frame.
    if frame_queue.full():
        try: frame_queue.get_nowait()
        except Empty: pass
    try:
        frame_queue.put_nowait(img)
        stats.on_enq()
    except Exception:
        pass


def ear(eye_idx: list, lm, sz: int) -> float:
    """Eye Aspect Ratio from 6 MediaPipe landmarks scaled to sz×sz."""
    pts = [(lm.landmark[i].x * sz, lm.landmark[i].y * sz) for i in eye_idx]
    a = math.dist(pts[1], pts[5])
    b = math.dist(pts[2], pts[4])
    c = math.dist(pts[0], pts[3])
    return (a + b) / (2.0 * c) if c > 0 else 0.0


def render_debug(frame: np.ndarray, lm, ear_val: Optional[float],
                 status: dict, roi: tuple) -> Optional[bytes]:
    """Lightweight debug overlay: ROI box, eye dots, state text."""
    dbg   = frame.copy()
    drowsy = status["state"] == "DROWSY"
    color  = (0, 0, 255) if drowsy else (0, 255, 0)

    if drowsy:
        cv2.rectangle(dbg, (0, 0), (dbg.shape[1], 48), (0, 0, 160), -1)

    if lm:
        rx, ry, rw, rh = roi
        cv2.rectangle(dbg, (rx, ry), (rx + rw, ry + rh), (60, 60, 200), 1)
        for idx in LEFT_EYE + RIGHT_EYE:
            p = lm.landmark[idx]
            cv2.circle(dbg, (int(rx + p.x * rw), int(ry + p.y * rh)), 1, (0, 220, 220), -1)

    ear_s = f"{ear_val:.3f}" if ear_val is not None else "—"
    cv2.putText(dbg, status["state"],
                (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    cv2.putText(dbg, f"EAR {ear_s}  CF {status['closed_frames']}/{DROWSY_FRAMES}",
                (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (210, 210, 210), 1)

    ok, buf = cv2.imencode(".jpg", dbg, [cv2.IMWRITE_JPEG_QUALITY, DEBUG_QUALITY])
    return buf.tobytes() if ok else None


# ── CV worker ─────────────────────────────────────────────────────────────────

def cv_worker() -> None:
    logger.info("[CV] started  close=%.3f open=%.3f frames=%d", EAR_CLOSE, EAR_OPEN, DROWSY_FRAMES)
    fail_streak = 0
    frame_idx   = 0

    while not _stop.is_set():
        try:
            with mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=False,       # not needed for 6-point EAR
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            ) as mesh:
                fail_streak = 0
                logger.info("[CV] FaceMesh ready")

                while not _stop.is_set():
                    # Auto-reset stale state when stream is interrupted.
                    if stats.age_ms() > STALE_RESET_S * 1000 and stats.rx > 0:
                        drowsiness.stale_reset()

                    try:
                        frame = frame_queue.get(timeout=1.0)
                    except Empty:
                        continue

                    frame_idx += 1
                    h, w = frame.shape[:2]

                    # Resize full frame to MP_SIZE×MP_SIZE for MediaPipe.
                    # Simpler and faster than ROI crop + Haar at this resolution.
                    roi_frame = cv2.resize(frame, (MP_SIZE, MP_SIZE), interpolation=cv2.INTER_LINEAR)
                    roi_coords = (0, 0, w, h)

                    try:
                        results = mesh.process(cv2.cvtColor(roi_frame, cv2.COLOR_BGR2RGB))
                    except Exception as e:
                        logger.warning("[CV] MediaPipe: %s", e)
                        fail_streak += 1
                        continue

                    ear_val = lm = None
                    if results.multi_face_landmarks:
                        lm = results.multi_face_landmarks[0]
                        ear_val = (ear(LEFT_EYE, lm, MP_SIZE) + ear(RIGHT_EYE, lm, MP_SIZE)) / 2.0

                    drowsiness.update(ear_val)
                    status = drowsiness.get_status()

                    # Render debug frame every DEBUG_EVERY frames to reduce CPU.
                    if frame_idx % DEBUG_EVERY == 0:
                        jpeg = render_debug(frame, lm, ear_val, status, roi_coords)
                        if jpeg:
                            debug_store.put(jpeg)

                    if SHOW_DISPLAY:
                        cv2.imshow("DAS", roi_frame)
                        if cv2.waitKey(1) & 0xFF == 27:
                            _stop.set()
                            break

                    fail_streak = 0

        except Exception as e:
            fail_streak += 1
            wait = min(0.1 * (2 ** fail_streak), 3.0)
            logger.error("[CV] crash streak=%d retry=%.1fs: %s", fail_streak, wait, e, exc_info=True)
            if not _stop.is_set():
                time.sleep(wait)

    if SHOW_DISPLAY:
        cv2.destroyAllWindows()
    logger.info("[CV] stopped")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_cv_worker() -> None:
    global _cv_thread
    _stop.clear()
    _cv_thread = threading.Thread(target=cv_worker, daemon=True, name="cv-worker")
    _cv_thread.start()


def stop_cv_worker() -> None:
    _stop.set()
    if _cv_thread:
        _cv_thread.join(timeout=5)


# ── Routes ────────────────────────────────────────────────────────────────────

router = APIRouter()


@router.websocket("/ws/video")
async def ws_video(websocket: WebSocket) -> None:
    """Browser virtual camera stream (base64 JPEG over WebSocket)."""
    await websocket.accept()
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except (WebSocketDisconnect, Exception):
                break
            stats.on_rx()
            try:
                raw = base64.b64decode(data)
            except Exception:
                stats.on_decode(False)
                continue
            enqueue(decode_jpeg(raw))
    finally:
        try: await websocket.close()
        except Exception: pass


@router.post("/frame")
async def frame_upload(request: Request,
                       x_api_key: Optional[str] = Header(default=None)):
    """
    ESP32 frame ingestion endpoint.

    Accepts raw JPEG bytes. Returns HTTP 200 with X-Drowsy-State header
    so the ESP32 learns the current alert state from every POST response
    without a separate /status request.
    """
    require_api_key(x_api_key)

    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                raise HTTPException(413, "Frame too large")
        except ValueError:
            raise HTTPException(400, "Invalid content-length")

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "Frame too large")

    stats.on_rx()
    img = decode_jpeg(body)
    enqueue(img)
    if img is None:
        return Response(status_code=400)

    state = drowsiness.get_status()["state"]
    return Response(
        content=state.encode(),
        status_code=200,
        media_type="text/plain",
        headers={"X-Drowsy-State": state},
    )


@router.get("/status")
def get_status(x_api_key: Optional[str] = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return drowsiness.get_status()


@router.get("/metrics")
def get_metrics(x_api_key: Optional[str] = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return stats.snapshot()


@router.get("/health")
def get_health() -> dict:
    age     = stats.age_ms()
    ever_rx = stats.rx > 0
    alive   = _cv_thread is not None and _cv_thread.is_alive()
    return {
        "healthy":           not (age > 5000 and ever_rx) and alive,
        "stalled":           age > 5000 and ever_rx,
        "worker_alive":      alive,
        "last_frame_age_ms": age,
        "decode_fail_rate":  round(stats.fail_rate(), 3),
    }


@router.get("/debug/frame.jpg")
def get_debug_frame(x_api_key: Optional[str] = Header(default=None)) -> Response:
    require_api_key(x_api_key)
    jpeg = debug_store.get()
    if not jpeg:
        return Response(status_code=204)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/config")
def get_config() -> dict:
    return SETTINGS
