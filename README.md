AI based driver drowsiness detection and alert system built using ESP32 with OV3660 camera and Python.

The system monitors the driver’s eyes, detects prolonged eye closure, and triggers visual and audible alerts to indicate drowsiness.

#### Tech Stack
- ESP32-CAM (OV3660)  
- Python  
- OpenCV  
- MediaPipe  
- FastAPI  

#### Workflow
Camera → Face & eye landmark detection → Drowsiness classification → Alert (LED / buzzer)

#### Detection Logic
Eye Aspect Ratio (EAR) is computed from facial landmarks and evaluated over consecutive frames to classify the driver state as **NORMAL** or **DROWSY**.

For detailed architecture, design decisions, and implementation, refer to the `docs/` directory.

Developed as a micro project for college coursework.
