const video = document.getElementById("video");
const ws = new WebSocket("ws://localhost:8000/ws/video");

navigator.mediaDevices.getUserMedia({ video: true }).then((stream) => {
  video.srcObject = stream;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  setInterval(() => {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          ws.send(reader.result.split(",")[1]);
        };
        reader.readAsDataURL(blob);
      },
      "image/jpeg",
      0.6,
    );
  }, 100);
});
