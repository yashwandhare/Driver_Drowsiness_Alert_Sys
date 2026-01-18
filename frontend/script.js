const video = document.getElementById("video");
const greenLED = document.getElementById("green-led");
const redLED = document.getElementById("red-led");
const statusBox = document.getElementById("status");
const logBox = document.getElementById("log");

let ws;
let lastState = "NORMAL";
let audioCtx = null;
let oscillator = null;

// Sound alert
function startBuzzer() {
  if (audioCtx) return; // already running

  audioCtx = new AudioContext();
  oscillator = audioCtx.createOscillator();
  oscillator.type = "square";
  oscillator.frequency.value = 700;

  oscillator.connect(audioCtx.destination);
  oscillator.start();
}

function stopBuzzer() {
  if (!audioCtx) return;

  oscillator.stop();
  oscillator.disconnect();
  audioCtx.close();

  oscillator = null;
  audioCtx = null;
}

function log(msg) {
  const time = new Date().toLocaleTimeString();
  logBox.innerHTML += `[${time}] ${msg}<br>`;
  logBox.scrollTop = logBox.scrollHeight;
}

// Camera
navigator.mediaDevices
  .getUserMedia({ video: true })
  .then((stream) => {
    video.srcObject = stream;
    log("Camera initialized");
    startWebSocket();
  })
  .catch((err) => log("Camera error: " + err));

// WebSocket
function startWebSocket() {
  ws = new WebSocket("ws://127.0.0.1:8000/ws/video");

  ws.onopen = () => {
    greenLED.classList.add("on");
    log("Connected to backend");
    streamFrames();
  };

  ws.onclose = () => {
    greenLED.classList.remove("on");
    log("Backend disconnected");
  };
}

// Stream frames
function streamFrames() {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (video.videoWidth === 0) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0);

    const data = canvas.toDataURL("image/jpeg").split(",")[1];
    if (data) ws.send(data);
  }, 100);
}

// Poll status
// Poll status
setInterval(async () => {
  try {
    const res = await fetch("http://127.0.0.1:8000/status");
    const data = await res.json();

    statusBox.textContent = JSON.stringify(data, null, 2);

    if (data.state === "DROWSY") {
      redLED.classList.add("on");

      if (lastState !== "DROWSY") {
        startBuzzer();
        log("DROWSINESS DETECTED → Red LED ON, buzzer ON");
      }
    } else {
      if (lastState === "DROWSY") {
        stopBuzzer();
        log("Driver awake → Red LED OFF, buzzer OFF");
      }
      redLED.classList.remove("on");
    }

    lastState = data.state; // ← THIS WAS MISSING
  } catch (err) {
    greenLED.classList.remove("on");
    log("Backend unreachable");
  }
}, 1000);
document.body.addEventListener(
  "click",
  () => {
    if (!audioCtx) {
      audioCtx = new AudioContext();
      audioCtx.close();
      audioCtx = null;
      log("Audio unlocked");
    }
  },
  { once: true },
);
