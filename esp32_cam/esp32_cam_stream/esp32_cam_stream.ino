#include <Arduino.h>
#include <WiFi.h>
#include "esp_camera.h"

const char* WIFI_SSID     = "Bhakarwadi";
const char* WIFI_PASSWORD = "abcdefgh";
const char* SERVER_HOST   = "10.96.37.243";
const int   SERVER_PORT   = 8000;
const char* API_TOKEN     = "";
const bool  ENABLE_BUZZER = true;

#define BUZZER_PIN            13
#define STATUS_LED_PIN         4

#define FRAME_INTERVAL_MS     30
#define CONNECT_TIMEOUT_MS   600
#define READ_TIMEOUT_MS      800
#define RESPONSE_TIMEOUT_MS 1200
#define WIFI_SETTLE_MS       800
#define SERVER_TIMEOUT_MS   5000

#define MAX_RETRIES            2
#define RETRY_BASE_MS        120

#define LED_HB_ON_MS          12
#define LED_HB_OFF_MS       2988
#define LED_DROWSY_MS        120

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

bool          serverConnected   = false;
bool          isDrowsy          = false;
bool          wifiBeginIssued   = false;
bool          wifiSettled       = false;
bool          ledOn             = false;
bool          heartbeatPhaseOn  = false;
bool          sendBusy          = false;
wl_status_t   lastWiFiStatus    = WL_IDLE_STATUS;
unsigned long wifiConnectedAt   = 0;
unsigned long lastFrameMs       = 0;
unsigned long lastServerOkMs    = 0;
unsigned long lastWiFiAttemptMs = 0;
unsigned long lastHeartbeatMs   = 0;
unsigned long lastBlinkMs       = 0;
unsigned long nextSendAllowedMs = 0;
unsigned long lastLogMs         = 0;
int           retryAttempt      = 0;
int           captureFailStreak = 0;

// Persistent HTTP connection (keep-alive).
WiFiClient persistClient;
bool       persistValid = false;

void led(bool on) {
  digitalWrite(STATUS_LED_PIN, on ? HIGH : LOW);
  ledOn = on;
}

void buzzer(bool on) {
  digitalWrite(BUZZER_PIN, (ENABLE_BUZZER && on) ? HIGH : LOW);
}

void logNet(const char* tag, int code) {
  unsigned long now = millis();
  if (now - lastLogMs < 800) return;
  lastLogMs = now;
  Serial.printf("%s %d retry=%d\n", tag, code, retryAttempt);
}

int readLine(WiFiClient& client, char* buf, int bufSize, unsigned long deadlineMs) {
  int pos = 0;
  while (millis() < deadlineMs) {
    if (client.available()) {
      char c = client.read();
      if (c == '\n') {
        if (pos > 0 && buf[pos - 1] == '\r') pos--;
        buf[pos] = '\0';
        return pos;
      }
      if (pos < bufSize - 1) {
        buf[pos++] = c;
      }
    } else {
      if (!client.connected()) break;
      delay(1);
    }
  }
  buf[pos] = '\0';
  return (pos > 0) ? pos : -1;
}

bool startsWithCI(const char* str, const char* prefix) {
  while (*prefix) {
    if (tolower((unsigned char)*str) != tolower((unsigned char)*prefix)) return false;
    str++;
    prefix++;
  }
  return true;
}

bool parseStateFromBuf(const char* buf, bool* out) {
  if (strstr(buf, "DROWSY")) {
    *out = true;
    return true;
  }
  if (strstr(buf, "NORMAL")) {
    *out = false;
    return true;
  }
  return false;
}

bool readHttpStateFromClient(WiFiClient& client, int* statusCode, bool* stateSeen, bool* stateValue) {
  unsigned long deadline = millis() + RESPONSE_TIMEOUT_MS;
  char lineBuf[256];

  // Read status line.
  int len = readLine(client, lineBuf, sizeof(lineBuf), deadline);
  if (len < 0 || strncmp(lineBuf, "HTTP/1.", 7) != 0) {
    *statusCode = -11;
    return false;
  }

  char* sp1 = strchr(lineBuf, ' ');
  if (!sp1) { *statusCode = -11; return false; }
  *statusCode = atoi(sp1 + 1);

  int contentLength = -1;

  while (millis() < deadline) {
    len = readLine(client, lineBuf, sizeof(lineBuf), deadline);
    if (len <= 0) break;

    char* colon = strchr(lineBuf, ':');
    if (!colon) continue;

    *colon = '\0';
    char* key = lineBuf;
    char* val = colon + 1;
    while (*val == ' ') val++;

    if (strcasecmp(key, "content-length") == 0) {
      contentLength = atoi(val);
    } else if (strcasecmp(key, "x-drowsy-state") == 0) {
      bool stateVal = false;
      if (parseStateFromBuf(val, &stateVal)) {
        *stateSeen = true;
        *stateValue = stateVal;
      }
    }
  }

  char bodyBuf[128];
  int bodyPos = 0;
  int bodyLimit = (contentLength >= 0 && contentLength < (int)sizeof(bodyBuf))
                  ? contentLength : (int)sizeof(bodyBuf) - 1;

  while (millis() < deadline && bodyPos < bodyLimit) {
    if (client.available()) {
      bodyBuf[bodyPos++] = client.read();
    } else {
      if (!client.connected()) break;
      delay(1);
    }
  }
  bodyBuf[bodyPos] = '\0';

  if (!*stateSeen && bodyPos > 0) {
    bool stateVal = false;
    if (parseStateFromBuf(bodyBuf, &stateVal)) {
      *stateSeen = true;
      *stateValue = stateVal;
    }
  }

  return true;
}

bool cameraInit() {
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
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_QVGA;
  c.jpeg_quality = 11;
  c.fb_count     = psramFound() ? 2 : 1;
  c.grab_mode    = psramFound() ? CAMERA_GRAB_LATEST : CAMERA_GRAB_WHEN_EMPTY;
  c.fb_location  = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

  if (esp_camera_init(&c) != ESP_OK) {
    Serial.println("Camera init failed");
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    Serial.println("Sensor null");
    esp_camera_deinit();
    return false;
  }

  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 0);
    s->set_contrast(s, 1);
    s->set_saturation(s, 0);
    s->set_denoise(s, 2);
    s->set_gain_ctrl(s, 1);
    s->set_framesize(s, FRAMESIZE_QVGA);
    s->set_quality(s, 11);
  }

  Serial.printf("Camera ready (PSRAM: %s, fb_count: %d)\n",
                psramFound() ? "yes" : "no", c.fb_count);
  return true;
}

void wifiMaintain() {
  wl_status_t status = WiFi.status();

  if (status != lastWiFiStatus) {
    lastWiFiStatus = status;
    if (status == WL_CONNECTED) {
      wifiConnectedAt = millis();
      wifiSettled = false;
      Serial.printf("WiFi up: %s\n", WiFi.localIP().toString().c_str());
    } else {
      wifiSettled = false;
      retryAttempt = 0;
    }
  }

  if (status == WL_CONNECTED) {
    if (!wifiSettled && millis() - wifiConnectedAt >= WIFI_SETTLE_MS) {
      wifiSettled = true;
      nextSendAllowedMs = millis();
      Serial.printf("Ready POST -> http://%s:%d/frame\n", SERVER_HOST, SERVER_PORT);
    }
    return;
  }

  unsigned long now = millis();
  if (!wifiBeginIssued) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setTxPower(WIFI_POWER_13dBm);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiBeginIssued = true;
    lastWiFiAttemptMs = now;
    Serial.println("WiFi connecting");
    return;
  }

  bool terminal = (status == WL_DISCONNECTED || status == WL_CONNECT_FAILED ||
                   status == WL_NO_SSID_AVAIL || status == WL_CONNECTION_LOST);
  if (!terminal || now - lastWiFiAttemptMs < 5000) return;

  lastWiFiAttemptMs = now;
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("WiFi reconnect");
}

bool sendFrameOnce(int* outCode) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    if (++captureFailStreak >= 5) {
      captureFailStreak = 0;
      Serial.println("Camera restart");
      esp_camera_deinit();
      delay(80);
      cameraInit();
    }
    *outCode = -21;
    return false;
  }
  captureFailStreak = 0;

  size_t frameLen = fb->len;

  bool reused = persistValid && persistClient.connected();
  if (!reused) {
    persistClient.stop();
    persistValid = false;
    persistClient.setNoDelay(true);
    persistClient.setTimeout(READ_TIMEOUT_MS);
    if (!persistClient.connect(SERVER_HOST, SERVER_PORT, CONNECT_TIMEOUT_MS)) {
      esp_camera_fb_return(fb);
      *outCode = -1;
      return false;
    }
  }

  char hdr[320];
  int hlen;
  if (strlen(API_TOKEN) > 0) {
    hlen = snprintf(hdr, sizeof(hdr),
      "POST /frame HTTP/1.1\r\n"
      "Host: %s:%d\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: %u\r\n"
      "X-API-Key: %s\r\n"
      "Connection: keep-alive\r\n\r\n",
      SERVER_HOST, SERVER_PORT, (unsigned)frameLen, API_TOKEN);
  } else {
    hlen = snprintf(hdr, sizeof(hdr),
      "POST /frame HTTP/1.1\r\n"
      "Host: %s:%d\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: %u\r\n"
      "Connection: keep-alive\r\n\r\n",
      SERVER_HOST, SERVER_PORT, (unsigned)frameLen);
  }
  if (persistClient.write((uint8_t*)hdr, hlen) != (size_t)hlen) {
    persistClient.stop();
    persistValid = false;
    esp_camera_fb_return(fb);
    *outCode = -2;
    return false;
  }

  size_t sent = 0;
  unsigned long writeStart = millis();
  while (sent < frameLen) {
    size_t chunk = frameLen - sent;
    if (chunk > 4096) chunk = 4096;
    size_t n = persistClient.write(fb->buf + sent, chunk);
    if (n == 0) {
      if (!persistClient.connected()) break;
      if (millis() - writeStart > READ_TIMEOUT_MS) break;
      delay(1);
      continue;
    }
    sent += n;
    if (millis() - writeStart > READ_TIMEOUT_MS) break;
  }

  esp_camera_fb_return(fb);
  fb = NULL;

  if (sent != frameLen) {
    persistClient.stop();
    persistValid = false;
    *outCode = -3;
    return false;
  }

  bool stateSeen = false;
  bool stateVal = false;
  int code = -11;
  bool parsed = readHttpStateFromClient(persistClient, &code, &stateSeen, &stateVal);

  if (!parsed || (code != 200 && code != 204)) {
    persistClient.stop();
    persistValid = false;
    if (!parsed) {
      *outCode = code;
      return false;
    }
  } else {
    persistValid = true;
  }

  if (stateSeen) {
    isDrowsy = stateVal;
  }

  *outCode = code;
  return (code == 200 || code == 204);
}

void onFrameResult(bool ok, int code) {
  unsigned long now = millis();

  if (ok) {
    lastServerOkMs = now;
    retryAttempt = 0;
    nextSendAllowedMs = now + FRAME_INTERVAL_MS;
    logNet(isDrowsy ? "OK:DROWSY" : "OK:NORMAL", code);
    return;
  }

  logNet("Frame", code);

  if (retryAttempt < MAX_RETRIES) {
    retryAttempt++;
    unsigned long backoff = RETRY_BASE_MS << (retryAttempt - 1);
    if (backoff > 640) backoff = 640;
    nextSendAllowedMs = now + backoff;
  } else {
    retryAttempt = 0;
    nextSendAllowedMs = now + FRAME_INTERVAL_MS;
  }
}

void updateAlerts() {
  unsigned long now = millis();
  buzzer(serverConnected && isDrowsy);

  if (!wifiSettled) {
    led(false);
    heartbeatPhaseOn = false;
    return;
  }

  if (isDrowsy && serverConnected) {
    if (now - lastBlinkMs >= LED_DROWSY_MS) {
      led(!ledOn);
      lastBlinkMs = now;
    }
    heartbeatPhaseOn = false;
    lastHeartbeatMs = now;
    return;
  }

  unsigned long elapsed = now - lastHeartbeatMs;
  if (!heartbeatPhaseOn && elapsed >= LED_HB_OFF_MS) {
    heartbeatPhaseOn = true;
    lastHeartbeatMs = now;
    led(true);
  } else if (heartbeatPhaseOn && elapsed >= LED_HB_ON_MS) {
    heartbeatPhaseOn = false;
    lastHeartbeatMs = now;
    led(false);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(STATUS_LED_PIN, OUTPUT);
  buzzer(false);
  led(false);

  if (!cameraInit()) {
    Serial.println("FATAL: camera init failed");
    while (true) { led(true); delay(150); led(false); delay(150); }
  }

  wifiMaintain();
}

void loop() {
  wifiMaintain();
  unsigned long now = millis();

  bool ready = WiFi.status() == WL_CONNECTED && wifiSettled;

  if (ready && !sendBusy && now >= nextSendAllowedMs) {
    sendBusy = true;
    int code = -99;
    bool ok = sendFrameOnce(&code);
    sendBusy = false;
    onFrameResult(ok, code);
  }

  serverConnected = (now - lastServerOkMs) <= SERVER_TIMEOUT_MS;
  updateAlerts();
}
