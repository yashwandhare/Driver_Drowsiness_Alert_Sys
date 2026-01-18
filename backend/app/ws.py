import base64
import math
from queue import Queue
from threading import Thread

import cv2
import mediapipe as mp
import numpy as np
from fastapi import APIRouter, WebSocket

EAR_THRESHOLD = 0.20
DROWSY_FRAMES = 20  # ~2 seconds at ~10 FPS

closed_frames = 0
state = "NORMAL"

router = APIRouter()

# Queue keeps only the latest frame (real-time behavior)
frame_queue = Queue(maxsize=1)

# MediaPipe setup
mp_face_mesh = mp.solutions.face_mesh
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

# Initialize Face Mesh model once
face_mesh_model = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)

# Eye landmark indices (MediaPipe standard)
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]


# Distance between two points
def euclidean(p1, p2):
    return math.dist(p1, p2)


# Compute Eye Aspect Ratio
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


# Worker thread for CV processing
def cv_worker():
    global closed_frames, state
    while True:
        frame = frame_queue.get()
        if frame is None:
            break

        # Convert frame to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh_model.process(rgb)

        if results.multi_face_landmarks:
            for landmarks in results.multi_face_landmarks:
                # Draw face mesh
                mp_draw.draw_landmarks(
                    image=frame,
                    landmark_list=landmarks,
                    connections=mp_face_mesh.FACEMESH_TESSELATION,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_style.get_default_face_mesh_tesselation_style(),
                )

                # Compute EAR
                h, w, _ = frame.shape
                left_ear = compute_ear(LEFT_EYE, landmarks, w, h)
                right_ear = compute_ear(RIGHT_EYE, landmarks, w, h)
                ear = (left_ear + right_ear) / 2.0

                if ear < EAR_THRESHOLD:
                    closed_frames += 1
                else:
                    closed_frames = 0
                    state = "NORMAL"

                if closed_frames >= DROWSY_FRAMES:
                    state = "DROWSY"

                # Display EAR
                cv2.putText(
                    frame,
                    f"STATE: {state}",
                    (30, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 0, 255) if state == "DROWSY" else (0, 255, 0),
                    2,
                )

        cv2.imshow("Simulated ESP32 Stream", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cv2.destroyAllWindows()


# WebSocket endpoint
@router.websocket("/ws/video")
async def video_ws(websocket: WebSocket):
    await websocket.accept()

    # Start CV worker thread
    Thread(target=cv_worker, daemon=True).start()

    try:
        while True:
            # Receive base64 frame from browser
            data = await websocket.receive_text()
            img_bytes = base64.b64decode(data)

            # Decode JPEG to OpenCV frame
            frame = cv2.imdecode(
                np.frombuffer(img_bytes, np.uint8),
                cv2.IMREAD_COLOR,
            )

            if frame is None:
                continue

            # Drop old frame if queue is full
            if frame_queue.full():
                frame_queue.get_nowait()

            frame_queue.put(frame)

    except Exception as e:
        print("WS closed:", e)

    finally:
        frame_queue.put(None)
