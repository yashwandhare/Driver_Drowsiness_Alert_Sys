import base64
from queue import Queue
from threading import Thread

import cv2
import mediapipe as mp
import numpy as np
from fastapi import APIRouter, WebSocket

router = APIRouter()
frame_queue = Queue(maxsize=1)

mp_face_mesh = mp.solutions.face_mesh
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

face_mesh_model = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)


def cv_worker():
    while True:
        frame = frame_queue.get()
        if frame is None:
            break

        # Convert to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh_model.process(rgb)

        if results.multi_face_landmarks:
            for landmarks in results.multi_face_landmarks:
                mp_draw.draw_landmarks(
                    image=frame,
                    landmark_list=landmarks,
                    connections=mp_face_mesh.FACEMESH_TESSELATION,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_style.get_default_face_mesh_tesselation_style(),
                )

        cv2.imshow("Simulated ESP32 Stream", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cv2.destroyAllWindows()


@router.websocket("/ws/video")
async def video_ws(websocket: WebSocket):
    await websocket.accept()

    worker = Thread(target=cv_worker, daemon=True)
    worker.start()

    try:
        while True:
            data = await websocket.receive_text()

            img_bytes = base64.b64decode(data)
            np_img = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(np_img, cv2.IMREAD_COLOR)

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
