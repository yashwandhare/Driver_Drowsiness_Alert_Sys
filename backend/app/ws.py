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
from starlette.requests import ClientDisconnect

from app.config import SETTINGS

logger = logging.getLogger("das")

# ── Detection config ──────────────────────────────────────────────────────────
EAR_CLOSE      = float(SETTINGS["detection"]["ear_close_threshold"])
EAR_OPEN       = float(SETTINGS["detection"]["ear_open_threshold"])
DROWSY_SECONDS = float(SETTINGS["detection"]["drowsy_seconds"])
RECOVER_SECONDS= float(SETTINGS["detection"]["recover_seconds"])
SHOW_DISPLAY   = bool(SETTINGS["detection"]["show_display"])
MP_SIZE        = int(SETTINGS["detection"]["mp_input_size"])
DEBUG_EVERY_N  = max(1, int(SETTINGS["detection"]["debug_every_n"]))
API_TOKEN      = SETTINGS["network"].get("api_token", "").strip()

MAX_BODY_BYTES = 10 * 1024 * 1024
STALE_RESET_S  = 8
DEBUG_QUALITY  = 75

# EMA alpha: 0.35 keeps recent frames relevant but smooths out blink noise.
EAR_EMA_ALPHA = 0.35

# No-face reset: drop to NORMAL after this many ms without a detected face.
NO_FACE_RESET_MS = 1500

LEFT_EYE  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

mp_face_mesh = mp.solutions.face_mesh


# ── Drowsiness state machine ──────────────────────────────────────────────────

class DrowsinessState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state        = "NORMAL"
        self.alert_active = False
        self.ear_raw      = 0.0
        self.ear_ema: Optional[float] = None
        self.closed_frames = 0
        self._closed_since_ms:  Optional[int] = None
        self._recover_since_ms: Optional[int] = None
        self._no_face_since_ms: Optional[int] = None

    def update(self, ear: Optional[float], now_ms: int) -> None:
        with self._lock:
            if ear is None:
                if self._no_face_since_ms is None:
                    self._no_face_since_ms = now_ms
                elif now_ms - self._no_face_since_ms >= NO_FACE_RESET_MS:
                    self._reset_unlocked()
                return

            self._no_face_since_ms = None
            self.ear_raw = ear
            self.ear_ema = (
                EAR_EMA_ALPHA * ear + (1 - EAR_EMA_ALPHA) * self.ear_ema
                if self.ear_ema is not None else ear
            )
            ema = self.ear_ema

            if ema < EAR_CLOSE:
                self.closed_frames += 1
                self._recover_since_ms = None
                if self._closed_since_ms is None:
                    self._closed_since_ms = now_ms
                if now_ms - self._closed_since_ms >= int(DROWSY_SECONDS * 1000):
                    if self.state != "DROWSY":
                        logger.warning("DROWSY — ear=%.3f ema=%.3f", ear, ema)
                    self.state        = "DROWSY"
                    self.alert_active = True
                return

            if ema > EAR_OPEN:
                self._closed_since_ms = None
                self.closed_frames    = 0
                if self.state == "DROWSY":
                    if self._recover_since_ms is None:
                        self._recover_since_ms = now_ms
                    if now_ms - self._recover_since_ms >= int(RECOVER_SECONDS * 1000):
                        self._reset_unlocked()
                else:
                    self._recover_since_ms = None
                    self.state             = "NORMAL"
                    self.alert_active      = False
                return

            # EAR in dead zone — hold current state, decay closed count slowly.
            self.closed_frames = max(0, self.closed_frames - 1)

    def _reset_unlocked(self) -> None:
        self.state             = "NORMAL"
        self.alert_active      = False
        self.closed_frames     = 0
        self.ear_ema           = None
        self._closed_since_ms  = None
        self._recover_since_ms = None
        self._no_face_since_ms = None

    def stale_reset(self) -> None:
        with self._lock:
            self._reset_unlocked()
        logger.info("[CV] stale reset — no frames for %ds", STALE_RESET_S)

    def get_status(self) -> dict:
        with self._lock:
            now_ms     = int(time.monotonic() * 1000)
            closed_ms  = 0 if self._closed_since_ms is None else max(0, now_ms - self._closed_since_ms)
            return {
                "state":        self.state,
                "closed_frames":self.closed_frames,
                "closed_ms":    closed_ms,
                "ear_current":  round(self.ear_raw, 3),
                "ear_ema":      round(self.ear_ema, 3) if self.ear_ema is not None else None,
                "alert_active": self.alert_active,
            }


# ── Stream statistics ─────────────────────────────────────────────────────────

class StreamStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frame_rx_total          = 0
        self.frame_decode_fail_total = 0
        self.frame_enqueue_total     = 0
        self.frame_queue_drop_total  = 0
        self.frame_processed_total   = 0
        self.last_frame_ms           = 0
        self.last_processed_ms       = 0
        self.decode_recent: list     = []
        self.process_latency_ema_ms  = 0.0
        self.ingest_fps_ema          = 0.0
        self.process_fps_ema         = 0.0
        self._last_rx_ts             = 0.0
        self._last_proc_ts           = 0.0

    def on_rx(self, now_ms: int) -> None:
        with self._lock:
            self.frame_rx_total += 1
            self.last_frame_ms   = now_ms
            now_s = now_ms / 1000.0
            if self._last_rx_ts > 0:
                dt = now_s - self._last_rx_ts
                if dt > 0:
                    fps = 1.0 / dt
                    self.ingest_fps_ema = fps if self.ingest_fps_ema == 0 else (0.35 * fps + 0.65 * self.ingest_fps_ema)
            self._last_rx_ts = now_s

    def on_decode(self, ok: bool) -> None:
        with self._lock:
            if not ok:
                self.frame_decode_fail_total += 1
            self.decode_recent.append(ok)
            if len(self.decode_recent) > 50:
                self.decode_recent.pop(0)

    def on_enqueue(self) -> None:
        with self._lock:
            self.frame_enqueue_total += 1

    def on_queue_drop(self) -> None:
        with self._lock:
            self.frame_queue_drop_total += 1

    def on_processed(self, process_ms: float, now_ms: int) -> None:
        with self._lock:
            self.frame_processed_total += 1
            self.last_processed_ms      = now_ms
            self.process_latency_ema_ms = (
                process_ms if self.process_latency_ema_ms == 0
                else (0.30 * process_ms + 0.70 * self.process_latency_ema_ms)
            )
            now_s = now_ms / 1000.0
            if self._last_proc_ts > 0:
                dt = now_s - self._last_proc_ts
                if dt > 0:
                    fps = 1.0 / dt
                    self.process_fps_ema = fps if self.process_fps_ema == 0 else (0.35 * fps + 0.65 * self.process_fps_ema)
            self._last_proc_ts = now_s

    def fail_rate(self) -> float:
        with self._lock:
            if not self.decode_recent:
                return 0.0
            return self.decode_recent.count(False) / len(self.decode_recent)

    def age_ms(self) -> int:
        with self._lock:
            return (int(time.time() * 1000) - self.last_frame_ms) if self.last_frame_ms else 0

    def snapshot(self, status: dict) -> dict:
        with self._lock:
            recent = list(self.decode_recent)
            data = {
                "frame_rx_total":          self.frame_rx_total,
                "frame_decode_fail_total": self.frame_decode_fail_total,
                "frame_enqueue_total":     self.frame_enqueue_total,
                "frame_queue_drop_total":  self.frame_queue_drop_total,
                "frame_processed_total":   self.frame_processed_total,
                "last_frame_ms":           self.last_frame_ms,
                "last_processed_ms":       self.last_processed_ms,
                "ingest_fps":              round(self.ingest_fps_ema, 2),
                "process_fps":             round(self.process_fps_ema, 2),
                "process_latency_ms":      round(self.process_latency_ema_ms, 1),
                "decode_fail_rate":        round(recent.count(False) / len(recent), 3) if recent else 0.0,
            }
        data["last_frame_age_ms"] = self.age_ms()
        data.update(status)
        return data


# ── Debug frame store ─────────────────────────────────────────────────────────

class DebugStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        # Track when the frontend last fetched — guarded by the same lock.
        self._last_fetch_ms: int = 0

    def put(self, data: bytes) -> None:
        with self._lock:
            self._jpeg = data

    def get(self) -> Optional[bytes]:
        with self._lock:
            self._last_fetch_ms = int(time.time() * 1000)
            return self._jpeg

    def is_active(self) -> bool:
        with self._lock:
            return (int(time.time() * 1000) - self._last_fetch_ms) <= 4000


# ── Globals ───────────────────────────────────────────────────────────────────

drowsiness  = DrowsinessState()
stats       = StreamStats()
debug_store = DebugStore()
frame_queue: Queue = Queue(maxsize=1)
_stop       = threading.Event()
_cv_thread: Optional[threading.Thread] = None


# ── Utilities ─────────────────────────────────────────────────────────────────

def require_api_key(key: Optional[str]) -> None:
    if API_TOKEN and key != API_TOKEN:
        raise HTTPException(401, "Invalid API key")


def decode_jpeg(data: bytes) -> Optional[np.ndarray]:
    if not data:
        return None
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def enqueue(img: Optional[np.ndarray]) -> None:
    stats.on_decode(img is not None)
    if img is None:
        return
    if frame_queue.full():
        try:
            frame_queue.get_nowait()
            stats.on_queue_drop()
        except Empty:
            pass
    try:
        frame_queue.put_nowait(img)
        stats.on_enqueue()
    except Exception:
        stats.on_queue_drop()


def ear(eye_idx: list, lm, sz: int) -> float:
    pts = [(lm.landmark[i].x * sz, lm.landmark[i].y * sz) for i in eye_idx]
    a = math.dist(pts[1], pts[5])
    b = math.dist(pts[2], pts[4])
    c = math.dist(pts[0], pts[3])
    return (a + b) / (2.0 * c) if c > 0 else 0.0


def render_debug(frame: np.ndarray, lm, ear_val: Optional[float], status: dict) -> Optional[bytes]:
    dbg   = frame.copy()
    drowsy = status["state"] == "DROWSY"
    color  = (0, 0, 255) if drowsy else (0, 255, 0)

    if lm:
        h, w = dbg.shape[:2]
        for idx in LEFT_EYE + RIGHT_EYE:
            p = lm.landmark[idx]
            cv2.circle(dbg, (int(p.x * w), int(p.y * h)), 1, (0, 220, 220), -1)

    ear_s = f"{ear_val:.3f}" if ear_val is not None else "-"
    cv2.putText(dbg, status["state"], (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    cv2.putText(dbg, f"EAR {ear_s}  C {status['closed_ms']}ms",
                (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (210, 210, 210), 1)

    ok, buf = cv2.imencode(".jpg", dbg, [cv2.IMWRITE_JPEG_QUALITY, DEBUG_QUALITY])
    return buf.tobytes() if ok else None


# ── CV worker ─────────────────────────────────────────────────────────────────

def cv_worker() -> None:
    logger.info("[CV] started — close=%.3f open=%.3f drowsy=%.2fs recover=%.2fs mp=%d",
                EAR_CLOSE, EAR_OPEN, DROWSY_SECONDS, RECOVER_SECONDS, MP_SIZE)

    fail_streak = 0
    frame_idx   = 0

    while not _stop.is_set():
        try:
            with mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            ) as mesh:
                logger.info("[CV] FaceMesh ready")
                fail_streak = 0

                while not _stop.is_set():
                    # Stale reset if no frames for a while.
                    if stats.age_ms() > STALE_RESET_S * 1000 and stats.frame_rx_total > 0:
                        drowsiness.stale_reset()

                    try:
                        frame = frame_queue.get(timeout=0.05)
                    except Empty:
                        continue

                    frame_idx += 1
                    start = time.perf_counter()

                    roi = cv2.resize(frame, (MP_SIZE, MP_SIZE), interpolation=cv2.INTER_LINEAR)

                    try:
                        results = mesh.process(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
                    except Exception as e:
                        logger.warning("[CV] MediaPipe failed: %s", e)
                        fail_streak += 1
                        continue

                    ear_val = None
                    lm      = None
                    if results.multi_face_landmarks:
                        lm      = results.multi_face_landmarks[0]
                        ear_val = (ear(LEFT_EYE, lm, MP_SIZE) + ear(RIGHT_EYE, lm, MP_SIZE)) / 2.0

                    now_ms = int(time.monotonic() * 1000)
                    drowsiness.update(ear_val, now_ms)
                    status = drowsiness.get_status()

                    process_ms = (time.perf_counter() - start) * 1000.0
                    stats.on_processed(process_ms, int(time.time() * 1000))

                    if debug_store.is_active() and frame_idx % DEBUG_EVERY_N == 0:
                        jpeg = render_debug(frame, lm, ear_val, status)
                        if jpeg:
                            debug_store.put(jpeg)

                    if SHOW_DISPLAY:
                        cv2.imshow("DAS", roi)
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
    await websocket.accept()
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning("[WS] receive error: %s", e)
                break

            now_ms = int(time.time() * 1000)
            stats.on_rx(now_ms)

            try:
                raw = base64.b64decode(data)
            except Exception:
                stats.on_decode(False)
                continue

            enqueue(decode_jpeg(raw))
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/frame")
async def frame_upload(request: Request, x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)

    started_ms = int(time.time() * 1000)

    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                raise HTTPException(413, "Frame too large")
        except ValueError:
            raise HTTPException(400, "Bad content-length")

    try:
        body = await request.body()
    except ClientDisconnect:
        stats.on_decode(False)
        return Response(status_code=499)

    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "Frame too large")

    stats.on_rx(started_ms)
    img = decode_jpeg(body)
    enqueue(img)

    status     = drowsiness.get_status()
    state      = status["state"]
    latency_ms = int(time.time() * 1000) - started_ms

    status_code = 200 if img is not None else 400
    return Response(
        content=state.encode(),
        status_code=status_code,
        media_type="text/plain",
        headers={
            "X-Drowsy-State":    state,
            "X-Server-Latency-Ms": str(latency_ms),
        },
    )


@router.get("/metrics")
def get_metrics(x_api_key: Optional[str] = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return stats.snapshot(drowsiness.get_status())


@router.get("/health")
def get_health() -> dict:
    age      = stats.age_ms()
    ever_rx  = stats.frame_rx_total > 0
    alive    = _cv_thread is not None and _cv_thread.is_alive()
    snap     = stats.snapshot(drowsiness.get_status())
    degraded = stats.fail_rate() > 0.35 or snap["process_latency_ms"] > 400
    return {
        "healthy":          not (age > 5000 and ever_rx) and alive and not degraded,
        "stalled":          age > 5000 and ever_rx,
        "degraded":         degraded,
        "worker_alive":     alive,
        "last_frame_age_ms":age,
        "decode_fail_rate": round(stats.fail_rate(), 3),
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
