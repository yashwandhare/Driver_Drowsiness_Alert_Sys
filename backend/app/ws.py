import base64
import math
import threading
from contextlib import asynccontextmanager
from queue import Empty, Queue

import cv2
import mediapipe as mp
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# ---------------- Configuration ----------------
EAR_CLOSE_THRESHOLD = 0.23  # Eye considered closed
EAR_OPEN_THRESHOLD = 0.26  # Eye considered open again
DROWSY_FRAMES = 20  # Frames needed to mark drowsy
SHOW_DISPLAY = True  # Disable for headless systems

# ---------------- MediaPipe Setup ----------------
mp_face_mesh = mp.solutions.face_mesh
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]


# ---------------- Drowsiness State ----------------
class DrowsinessState:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = "NORMAL"
        self.closed_frames = 0
        self.ear_value = 0.0
        self.alert_active = False  # prevents alert spam

    def update(self, ear_value):
        with self._lock:
            self.ear_value = ear_value

            if ear_value < EAR_CLOSE_THRESHOLD:
                self.closed_frames += 1
                if self.closed_frames >= DROWSY_FRAMES:
                    self.state = "DROWSY"

            elif ear_value > EAR_OPEN_THRESHOLD:
                self.closed_frames = 0
                self.state = "NORMAL"
                self.alert_active = False  # reset alert latch

            # Alert triggers only once per drowsy event
            if self.state == "DROWSY" and not self.alert_active:
                print("ALERT: Driver is drowsy")
                self.alert_active = True

    def get_status(self):
        with self._lock:
            return {
                "state": self.state,
                "closed_frames": self.closed_frames,
                "ear_current": round(self.ear_value, 3),
                "alert_active": self.alert_active,
            }


# ---------------- Globals ----------------
drowsiness_state = DrowsinessState()
frame_queue = Queue(maxsize=1)
running = True


# ---------------- Helper Functions ----------------
def euclidean(p1, p2):
    return math.dist(p1, p2)


def compute_ear(eye_idx, landmarks, w, h):
    pts = []
    for idx in eye_idx:
        lm = landmarks.landmark[idx]
        pts.append((int(lm.x * w), int(lm.y * h)))

    A = euclidean(pts[1], pts[5])
    B = euclidean(pts[2], pts[4])
    C = euclidean(pts[0], pts[3])

    if C == 0:
        return 0.0
    return (A + B) / (2.0 * C)


# ---------------- CV Worker Thread ----------------
def cv_worker():
    print("info: [CV Worker] Started")

    with mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as face_mesh:
        while running:
            try:
                frame = frame_queue.get(timeout=1.0)
            except Empty:
                continue

            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            ear = 1.0  # default = eyes open

            if results.multi_face_landmarks:
                for landmarks in results.multi_face_landmarks:
                    if SHOW_DISPLAY:
                        mp_draw.draw_landmarks(
                            image=frame,
                            landmark_list=landmarks,
                            connections=mp_face_mesh.FACEMESH_TESSELATION,
                            connection_drawing_spec=mp_style.get_default_face_mesh_tesselation_style(),
                            landmark_drawing_spec=None,
                        )

                    left = compute_ear(LEFT_EYE, landmarks, w, h)
                    right = compute_ear(RIGHT_EYE, landmarks, w, h)
                    ear = (left + right) / 2.0

            # Update drowsiness logic
            drowsiness_state.update(ear)

            # -------- Display --------
            status = drowsiness_state.get_status()
            color = (0, 0, 255) if status["state"] == "DROWSY" else (0, 255, 0)

            if SHOW_DISPLAY:
                cv2.putText(
                    frame,
                    f"State: {status['state']}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    color,
                    2,
                )
                cv2.putText(
                    frame,
                    f"EAR: {ear:.2f} | Frames: {status['closed_frames']}/{DROWSY_FRAMES}",
                    (20, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    1,
                )

                cv2.imshow("Server Monitor", frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

    if SHOW_DISPLAY:
        cv2.destroyAllWindows()
    print("info: [CV Worker] Stopped")


# ---------------- FastAPI Lifecycle ----------------
@asynccontextmanager
async def lifespan(app: APIRouter):
    t = threading.Thread(target=cv_worker, daemon=True)
    t.start()
    yield
    global running
    running = False
    t.join()


router = APIRouter(lifespan=lifespan)


# ---------------- WebSocket ----------------
@router.websocket("/ws/video")
async def video_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            img = cv2.imdecode(
                np.frombuffer(base64.b64decode(data), np.uint8),
                cv2.IMREAD_COLOR,
            )

            if img is not None:
                if frame_queue.full():
                    frame_queue.get_nowait()
                frame_queue.put(img)
    except WebSocketDisconnect:
        pass


# ---------------- REST API ----------------
@router.get("/status")
def get_status():
    return drowsiness_state.get_status()
