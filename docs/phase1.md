PHASE 1 – ESP32-CAM SIMULATION  (coz didn't buy components on time)

Goal:
Simulate ESP32-CAM MJPEG stream using browser webcam.

Components:
- Browser camera (getUserMedia)
- WebSocket transport
- FastAPI backend
- OpenCV frame receiver

Output:
- Live frames visible on backend
- Stable streaming loop

Success Criteria:
- No frame drops for 10+ minutes
- <200 ms latency
- No memory growth
