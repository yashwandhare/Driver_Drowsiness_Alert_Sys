import base64
import math
import threading
from contextlib import asynccontextmanager
from queue import Empty, Queue

import cv2
import mediapipe as mp
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# --- Configuration ---
EAR_CLOSE_THRESHOLD = 0.23  # Eyes considered closed below this
EAR_OPEN_THRESHOLD = 0.26  # Eyes considered open above this
DROWSY_FRAMES = 20  # Consecutive frames to trigger alarm
SHOW_DISPLAY = True  # Set False if running on a headless server

# --- MediaPipe Setup ---
mp_face_mesh = mp.solutions.face_mesh
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]


# --- State Management (Thread-Safe & Fixed Logic) ---
class DrowsinessState:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = "NORMAL"
        self.closed_frames = 0
        self.ear_value = 0.0

    def update(self, ear_value):
        """
        Updates state based on Eye Aspect Ratio (EAR).
        Logic:
        - EAR < 0.23: Eyes Closed -> Increment counter
        - EAR > 0.26: Eyes Open -> Reset counter AND state
        - 0.23 <= EAR <= 0.26: Unsure -> Do nothing (maintain counter)
        """
        with self._lock:
            self.ear_value = ear_value

            if ear_value < EAR_CLOSE_THRESHOLD:
                # Eyes are strictly closed
                self.closed_frames += 1

                if self.closed_frames >= DROWSY_FRAMES:
                    self.state = "DROWSY"

            elif ear_value > EAR_OPEN_THRESHOLD:
                # Eyes are strictly open - RESET EVERYTHING
                self.closed_frames = 0
                self.state = "NORMAL"  # This was missing the reset!

            # If EAR is between 0.23 and 0.26, we do NOT change closed_frames.
            # We just hold the current count. This prevents flickering resets.

    def get_status(self):
        with self._lock:
            return {
                "state": self.state,
                "closed_frames": self.closed_frames,
                "ear_current": round(self.ear_value, 3),
            }


# Global instances
drowsiness_state = DrowsinessState()
frame_queue = Queue(maxsize=1)
running = True


# --- Helper Functions ---
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


# --- Background Worker ---
def cv_worker():
    print("info:    [CV Worker] Started")

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

            current_ear = 0.0

            if results.multi_face_landmarks:
                for landmarks in results.multi_face_landmarks:
                    # Draw mesh
                    if SHOW_DISPLAY:
                        mp_draw.draw_landmarks(
                            image=frame,
                            landmark_list=landmarks,
                            connections=mp_face_mesh.FACEMESH_TESSELATION,
                            connection_drawing_spec=mp_style.get_default_face_mesh_tesselation_style(),
                            landmark_drawing_spec=None,
                        )

                    # Calculate EAR
                    left_ear = compute_ear(LEFT_EYE, landmarks, w, h)
                    right_ear = compute_ear(RIGHT_EYE, landmarks, w, h)
                    current_ear = (left_ear + right_ear) / 2.0

            # Update state even when no face is detected (treats as eyes open)
            # When no face: EAR = 0, which is < 0.23, but we should handle this differently
            if results.multi_face_landmarks:
                drowsiness_state.update(current_ear)
            else:
                # No face detected - reset to normal after a moment
                drowsiness_state.update(1.0)  # High value = eyes definitely "open"

            # --- Visualization ---
            status = drowsiness_state.get_status()
            state_text = status["state"]
            frame_count = status["closed_frames"]

            color = (0, 0, 255) if state_text == "DROWSY" else (0, 255, 0)

            if SHOW_DISPLAY:
                # State Text
                cv2.putText(
                    frame,
                    f"State: {state_text}",
                    (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    color,
                    2,
                )

                # Debug Info
                debug_info = (
                    f"EAR: {current_ear:.2f} | Frames: {frame_count}/{DROWSY_FRAMES}"
                )
                cv2.putText(
                    frame,
                    debug_info,
                    (20, 90),
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
    print("info:    [CV Worker] Stopped")


# --- FastAPI Setup ---
@asynccontextmanager
async def lifespan(app: APIRouter):
    t = threading.Thread(target=cv_worker, daemon=True)
    t.start()
    yield
    global running
    running = False
    t.join()


router = APIRouter(lifespan=lifespan)


@router.websocket("/ws/video")
async def video_ws(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                img_bytes = base64.b64decode(data)
                np_arr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                if frame is not None:
                    if frame_queue.full():
                        frame_queue.get_nowait()
                    frame_queue.put(frame)
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS Error: {e}")


@router.get("/status")
def get_status():
    return drowsiness_state.get_status()
