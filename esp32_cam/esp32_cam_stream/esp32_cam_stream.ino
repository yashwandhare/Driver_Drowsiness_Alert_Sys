#include <Arduino.h>
#include <WiFi.h>
#include "esp_camera.h"

/* ESP32-CAM firmware for the drowsiness alert system. */

const char* WIFI_SSID       = "Bhakarwadi";
const char* WIFI_PASSWORD   = "abcdefgh";
const char* SERVER_HOST     = "10.96.37.243";
const int   SERVER_PORT     = 8000;
const char* API_TOKEN       = "";
const bool  ENABLE_BUZZER   = false;

#define BUZZER_PIN          13
#define STATUS_LED_PIN       4

#define FRAME_INTERVAL_MS   135
#define FRAME_MIN_MS        110
#define FRAME_MAX_MS        300
#define STATUS_POLL_MS     4000
#define CONNECT_TIMEOUT_MS  800
#define READ_TIMEOUT_MS     900
#define WIFI_SETTLE_MS     1500
#define SERVER_TIMEOUT_MS  8000
#define FAIL_THRESHOLD        6
#define BACKOFF_MS          600

#define LED_HB_ON_MS         12
#define LED_HB_OFF_MS      2988
#define LED_DROWSY_MS       120

#define PWDN_GPIO_NUM    32
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM     0
#define SIOD_GPIO_NUM    26
#define SIOC_GPIO_NUM    27
#define Y9_GPIO_NUM      35
#define Y8_GPIO_NUM      34
#define Y7_GPIO_NUM      39
#define Y6_GPIO_NUM      36
#define Y5_GPIO_NUM      21
#define Y4_GPIO_NUM      19
#define Y3_GPIO_NUM      18
#define Y2_GPIO_NUM       5
#define VSYNC_GPIO_NUM   25
#define HREF_GPIO_NUM    23
#define PCLK_GPIO_NUM    22

String        frameUrl, statusUrl;
WiFiClient    frameConn;
bool          serverConnected   = false;
bool          isDrowsy          = false;
bool          wifiBeginIssued   = false;
bool          wifiSettled       = false;
bool          ledOn             = false;
bool          heartbeatPhaseOn  = false;
wl_status_t   lastWiFiStatus    = WL_IDLE_STATUS;
unsigned long wifiConnectedAt   = 0;
unsigned long lastFrameMs       = 0;
unsigned long lastStatusMs      = 0;
unsigned long lastServerOkMs    = 0;
unsigned long lastWiFiAttemptMs = 0;
unsigned long lastHeartbeatMs   = 0;
unsigned long lastBlinkMs       = 0;
unsigned long backoffUntilMs    = 0;
unsigned long lastLogMs         = 0;
int           consecutiveFails  = 0;
int           captureFailStreak = 0;
int           frameIntervalMs   = FRAME_INTERVAL_MS;

void led(bool on) {
  digitalWrite(STATUS_LED_PIN, on ? HIGH : LOW);
  ledOn = on;
}

void buzzer(bool on) {
  digitalWrite(BUZZER_PIN, (ENABLE_BUZZER && on) ? HIGH : LOW);
}

void logNet(const char* tag, int code) {
  unsigned long now = millis();
  if (now - lastLogMs < 3000) return;
  lastLogMs = now;
  Serial.printf("%s %d  fails=%d\n", tag, code, consecutiveFails);
}

bool parseStateToken(const String& s, bool* out) {
  if (s.indexOf("DROWSY") >= 0) {
    *out = true;
    return true;
  }
  if (s.indexOf("NORMAL") >= 0) {
    *out = false;
    return true;
  }
  return false;
}

bool readHttpStateFromClient(WiFiClient& client, int* statusCode, bool* stateSeen, bool* stateValue) {
  String statusLine = client.readStringUntil('\n');
  statusLine.trim();
  if (!statusLine.startsWith("HTTP/1.")) {
    *statusCode = -11;
    return false;
  }

  int firstSp = statusLine.indexOf(' ');
  int secondSp = statusLine.indexOf(' ', firstSp + 1);
  if (firstSp < 0 || secondSp < 0) {
    *statusCode = -11;
    return false;
  }

  *statusCode = statusLine.substring(firstSp + 1, secondSp).toInt();

  int contentLength = -1;

  while (client.connected()) {
    String line = client.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) break;

    int colon = line.indexOf(':');
    if (colon > 0) {
      String key = line.substring(0, colon);
      String val = line.substring(colon + 1);
      key.trim();
      val.trim();
      key.toLowerCase();
      if (key == "content-length") {
        contentLength = val.toInt();
      }
      if (key == "x-drowsy-state") {
        bool parsed = false;
        bool stateVal = false;
        parsed = parseStateToken(val, &stateVal);
        if (parsed) {
          *stateSeen = true;
          *stateValue = stateVal;
        }
      }
    }
  }

  String body;
  unsigned long start = millis();
  while ((millis() - start) < READ_TIMEOUT_MS) {
    while (client.available()) {
      body += (char)client.read();
      if (contentLength >= 0 && (int)body.length() >= contentLength) {
        break;
      }
    }
    if (contentLength >= 0 && (int)body.length() >= contentLength) break;
    if (!client.connected()) break;
    delay(1);
  }

  if (!*stateSeen && body.length() > 0) {
    bool stateVal = false;
    if (parseStateToken(body, &stateVal)) {
      *stateSeen = true;
      *stateValue = stateVal;
    }
  }

  return true;
}

void closeFrameConn() {
  if (frameConn.connected()) {
    frameConn.stop();
  }
}

bool ensureFrameConn() {
  if (frameConn.connected()) return true;
  frameConn.setNoDelay(true);
  frameConn.setTimeout(READ_TIMEOUT_MS);
  return frameConn.connect(SERVER_HOST, SERVER_PORT, CONNECT_TIMEOUT_MS);
}

// ── Camera ────────────────────────────────────────────────────────────────────

bool cameraInit() {
  // Power-cycle sensor to clear stale I2C state on warm boot.
  pinMode(PWDN_GPIO_NUM, OUTPUT);
  digitalWrite(PWDN_GPIO_NUM, HIGH); delay(10);
  digitalWrite(PWDN_GPIO_NUM, LOW);  delay(10);

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk  = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_QVGA;  // 320×240 — small payload, enough for EAR
  c.jpeg_quality = 15;               // 10–20: lower = larger file, higher quality
  c.fb_count     = 1;
  c.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;  // DMA only runs on fb_get()
  c.fb_location  = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

  if (esp_camera_init(&c) != ESP_OK) { Serial.println("Camera init failed"); return false; }

  sensor_t* s = esp_camera_sensor_get();
  if (!s) { Serial.println("Sensor null"); esp_camera_deinit(); return false; }

  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 0);
    s->set_contrast(s, 1);
    s->set_saturation(s, 0);
    s->set_sharpness(s, 0);
    s->set_denoise(s, 2);
    s->set_special_effect(s, 0);
    s->set_lenc(s, 0);
    s->set_gain_ctrl(s, 1);
    s->set_agc_gain(s, 8);
    s->set_framesize(s, FRAMESIZE_QVGA);
    s->set_quality(s, 15);
  }
  Serial.println("Camera ready");
  return true;
}

// ── WiFi ──────────────────────────────────────────────────────────────────────

void wifiMaintain() {
  wl_status_t status = WiFi.status();

  if (status != lastWiFiStatus) {
    lastWiFiStatus = status;
    if (status == WL_CONNECTED) {
      wifiConnectedAt = millis();
      wifiSettled     = false;
      Serial.printf("WiFi up: %s\n", WiFi.localIP().toString().c_str());
    } else {
      wifiSettled = false;
      closeFrameConn();
    }
  }

  if (status == WL_CONNECTED) {
    if (!wifiSettled && millis() - wifiConnectedAt >= WIFI_SETTLE_MS) {
      wifiSettled = true;
      Serial.printf("Ready — sending to http://%s:%d\n", SERVER_HOST, SERVER_PORT);
    }
    return;
  }

  unsigned long now = millis();
  if (!wifiBeginIssued) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setTxPower(WIFI_POWER_19_5dBm);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiBeginIssued   = true;
    lastWiFiAttemptMs = now;
    Serial.println("WiFi connecting");
    return;
  }

  bool terminal = (status == WL_DISCONNECTED || status == WL_CONNECT_FAILED
               ||  status == WL_NO_SSID_AVAIL || status == WL_CONNECTION_LOST);
  if (!terminal || now - lastWiFiAttemptMs < 5000) return;

  lastWiFiAttemptMs = now;
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("WiFi reconnect");
}

// ── Frame upload ──────────────────────────────────────────────────────────────

bool sendFrame() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    if (++captureFailStreak >= 5) {
      captureFailStreak = 0;
      Serial.println("Camera restart");
      esp_camera_deinit();
      delay(100);
      cameraInit();
    }
    return false;
  }
  captureFailStreak = 0;

  bool ok = false;
  int code = -1;

  for (int attempt = 0; attempt < 2 && !ok; attempt++) {
    if (!ensureFrameConn()) {
      code = -1;
      closeFrameConn();
      continue;
    }

    frameConn.printf("POST /frame HTTP/1.1\r\n");
    frameConn.printf("Host: %s:%d\r\n", SERVER_HOST, SERVER_PORT);
    frameConn.printf("Content-Type: image/jpeg\r\n");
    frameConn.printf("Content-Length: %u\r\n", (unsigned)fb->len);
    if (strlen(API_TOKEN) > 0) {
      frameConn.printf("X-API-Key: %s\r\n", API_TOKEN);
    }
    frameConn.printf("Connection: keep-alive\r\n\r\n");

    size_t sent = frameConn.write(fb->buf, fb->len);
    if (sent != fb->len) {
      code = -3;
      closeFrameConn();
      continue;
    }

    bool stateSeen = false;
    bool stateVal = false;
    if (!readHttpStateFromClient(frameConn, &code, &stateSeen, &stateVal)) {
      closeFrameConn();
      continue;
    }

    if (stateSeen) {
      isDrowsy = stateVal;
    }

    ok = (code == 200 || code == 204);
    if (!ok) {
      closeFrameConn();
    }
  }

  if (!ok) logNet("Frame", code);

  esp_camera_fb_return(fb);
  return ok;
}

// ── Status poll ───────────────────────────────────────────────────────────────
// Fallback for when frame response header is missing (e.g. network blip).

bool pollStatus() {
  int code = -1;
  WiFiClient client;
  client.setTimeout(READ_TIMEOUT_MS);
  if (!client.connect(SERVER_HOST, SERVER_PORT, CONNECT_TIMEOUT_MS)) {
    logNet("Status", -1);
    return false;
  }

  client.printf("GET /status HTTP/1.1\r\n");
  client.printf("Host: %s:%d\r\n", SERVER_HOST, SERVER_PORT);
  if (strlen(API_TOKEN) > 0) {
    client.printf("X-API-Key: %s\r\n", API_TOKEN);
  }
  client.printf("Connection: close\r\n\r\n");

  bool stateSeen = false;
  bool stateVal = false;
  if (!readHttpStateFromClient(client, &code, &stateSeen, &stateVal)) {
    client.stop();
    logNet("Status", code);
    return false;
  }
  client.stop();

  if (stateSeen) isDrowsy = stateVal;
  if (code != 200) {
    logNet("Status", code);
    return false;
  }
  return true;
}

// ── LED / alerts ──────────────────────────────────────────────────────────────
// LED behaviour:
//   WiFi down             → OFF
//   WiFi up, no server    → slow heartbeat (12ms / 3s) — board alive, no server
//   WiFi up, server ok    → slow heartbeat
//   Drowsy                → fast blink (120ms)

void updateAlerts() {
  unsigned long now = millis();
  buzzer(serverConnected && isDrowsy);

  if (!wifiSettled) {
    led(false);
    heartbeatPhaseOn = false;
    return;
  }

  if (isDrowsy && serverConnected) {
    // Fast blink regardless of heartbeat state.
    if (now - lastBlinkMs >= LED_DROWSY_MS) {
      led(!ledOn);
      lastBlinkMs = now;
    }
    heartbeatPhaseOn = false;
    lastHeartbeatMs  = now;
    return;
  }

  // Slow heartbeat: connected or waiting for server.
  unsigned long elapsed = now - lastHeartbeatMs;
  if (!heartbeatPhaseOn && elapsed >= LED_HB_OFF_MS) {
    heartbeatPhaseOn = true;
    lastHeartbeatMs  = now;
    led(true);
  } else if (heartbeatPhaseOn && elapsed >= LED_HB_ON_MS) {
    heartbeatPhaseOn = false;
    lastHeartbeatMs  = now;
    led(false);
  }
}

// ── Setup / loop ──────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(BUZZER_PIN,     OUTPUT);
  pinMode(STATUS_LED_PIN, OUTPUT);
  // Do NOT call ledcSetup/ledcAttach on STATUS_LED_PIN (GPIO4).
  buzzer(false);
  led(false);

  frameUrl  = String("http://") + SERVER_HOST + ":" + SERVER_PORT + "/frame";
  statusUrl = String("http://") + SERVER_HOST + ":" + SERVER_PORT + "/status";

  if (!cameraInit()) {
    Serial.println("FATAL: camera init failed");
    while (true) { led(true); delay(150); led(false); delay(150); }
  }

  wifiMaintain();
}

void loop() {
  wifiMaintain();
  unsigned long now = millis();

  // Non-blocking backoff pause.
  if (now < backoffUntilMs) { updateAlerts(); return; }

  bool ready = WiFi.status() == WL_CONNECTED && wifiSettled;

  if (ready && now - lastFrameMs >= (unsigned long)frameIntervalMs) {
    // Use adaptive pacing so unstable links stay responsive instead of collapsing.
    bool ok = sendFrame();
    if (ok) {
      lastServerOkMs = now;
      consecutiveFails = 0;
      frameIntervalMs = max(FRAME_MIN_MS, frameIntervalMs - 4);
    } else {
      consecutiveFails++;
      frameIntervalMs = min(FRAME_MAX_MS, frameIntervalMs + 16);
    }
    lastFrameMs = now;
  }

  // Fallback poll — only when frame responses aren't updating isDrowsy.
  if (ready && now - lastStatusMs >= STATUS_POLL_MS) {
    if (pollStatus()) lastServerOkMs = now;
    lastStatusMs = now;
  }

  if (ready && consecutiveFails >= FAIL_THRESHOLD) {
    Serial.printf("Backoff: %d fails interval=%dms\n", consecutiveFails, frameIntervalMs);
    consecutiveFails = 0;
    frameIntervalMs = min(FRAME_MAX_MS, frameIntervalMs + 30);
    backoffUntilMs   = millis() + BACKOFF_MS;
    closeFrameConn();
  }

  serverConnected = (now - lastServerOkMs) <= SERVER_TIMEOUT_MS;
  updateAlerts();
}
