/* 
  Mushroom Chamber Controller - No Wall Clock (boot-based time)
  -------------------------------------------------------------
  - Time base: power-on stopwatch using millis() -> secondsSinceBoot
  - Day emulation: secondsSinceBoot % 86400 acts as 00:00..23:59 of a synthetic day
  - I2C slave address: 0x08
  - DHT22 sensors on pins 5, 6, 7
  - Active-LOW SSR on pins 2 (humidifier), 3 (fan1), 4 (fan2), 30 s global stagger between toggle
  - 30s sampling, 7-min rolling average
  - Humidifier & Fan1 ON windows:
      * Both OFF at synthetic 02:00–04:00, 10:00–12:00, 18:00–20:00 (synthetic time)
      * Max continuous ON 6h, then forced OFF 2h
      * Windows repeat every 24h of uptime.
  - Fan2 logic:
      * ON if avg(zone2,zone3) >= ambient OR if ambient >= 28°C (emergency)
      * OFF if avg(zone2,zone3) < 25°C
  - Telemetry field "ts" is elapsed seconds since boot (string)
*/

#include <Arduino.h>
#include <Wire.h>
#include <DHT.h>
#include <EEPROM.h>

// ===== Pin and I2C Setup =====
#define I2C_ADDR 0x08

#define SSR_HUM_PIN 2    // Humidifier SSR control (active LOW)
#define SSR_FAN1_PIN 3   // Fan1 SSR control (active LOW)
#define SSR_FAN2_PIN 4   // Fan2 SSR control (active LOW)

#define DHT_ZONE1_PIN 5  // DHT22 ambient sensor
#define DHT_ZONE2_PIN 6  // DHT22 zone2
#define DHT_ZONE3_PIN 7  // DHT22 zone3
#define DHTTYPE DHT22

// ===== Timing and Environment =====
const unsigned long SAMPLE_INTERVAL_MS = 30000UL;      // Sensor sampling interval (30s)
const int SAMPLES_7MIN = 14;                           // Rolling avg buffer for 7 min window
const unsigned long SSR_STAGGER_MS = 30000UL;          // Minimum interval (30s) between any SSR toggles

const float HUM_LOW = 80.0f;       // Humidifier ON if avg humidity < HUM_LOW (%)
const float HUM_HIGH = 95.0f;      // Humidifier OFF if avg humidity > HUM_HIGH (%)
const float TEMP_TARGET = 25.0f;   // Fans OFF if avg temp drops below this
const float AMBIENT_EMERGENCY = 28.0f; // Emergency mode for ambient temp >= this

const unsigned long HUM_MAX_CONT_MS = 6UL * 3600UL * 1000UL; // Humidifier max continuous ON (6h)
const unsigned long HUM_FORCED_OFF_MS = 2UL * 3600UL * 1000UL; // Forced OFF after max ON time (2h)

const unsigned long EEPROM_CLEAR_INTERVAL_MS = 30UL * 60UL * 1000UL; // EEPROM clear interval (30 min)

// ===== State Tracking =====
DHT dht1(DHT_ZONE1_PIN, DHTTYPE);
DHT dht2(DHT_ZONE2_PIN, DHTTYPE);
DHT dht3(DHT_ZONE3_PIN, DHTTYPE);

struct Sample { float t; float h; bool valid; };
Sample ambBuf[SAMPLES_7MIN], z2Buf[SAMPLES_7MIN], z3Buf[SAMPLES_7MIN];
int sampleIndex = 0;
unsigned long lastSampleMillis = 0;

unsigned long bootMillis = 0;          // Used for uptime-based synthetic clock

bool ssrHumState = false;
bool ssrFan1State = false;
bool ssrFan2State = false;
unsigned long lastSSRChangeMillis = 0; // Last SSR output change time

unsigned long humOnStartMs = 0;        // When humidifier was last turned ON
unsigned long humAccumRunMs = 0;       // Cumulative humidifier ON time for current cycle
bool humInForcedOff = false;           // Forced OFF mode status
unsigned long humForcedOffStartMs = 0; // Forced OFF mode start time

// EEPROM addresses for relay state
const int EEPROM_ADDR_FLAG   = 0;
const int EEPROM_ADDR_STATES = 1;
unsigned long lastEepromClearMs = 0;

// I2C TX buffer for telemetry response
char txBuffer[256];
int txLen = 0;
int txIndex = 0;
bool txReady = false;

// ===== Synthetic Clock Helpers =====

/*
  Returns seconds since Arduino booted.
*/
static inline unsigned long secondsSinceBoot() {
  return (millis() - bootMillis) / 1000UL;
}

/*
  Returns seconds elapsed in current synthetic 24-hour "day" (modulo 86400).
*/
static inline unsigned long secondsOfSyntheticDay() {
  return secondsSinceBoot() % 86400UL;
}

/*
  Record a temperature/humidity sample into a rolling buffer.
*/
static inline void pushSample(Sample buf[], float t, float h) {
  buf[sampleIndex].t = t;
  buf[sampleIndex].h = h;
  buf[sampleIndex].valid = (!isnan(t) && !isnan(h));
}

/*
  Compute average temperature and humidity for a rolling buffer.
*/
static inline void computeAvg(Sample buf[], float &avgT, float &avgH) {
  float sT = 0.0f, sH = 0.0f; int cnt = 0;
  for (int i = 0; i < SAMPLES_7MIN; i++) {
    if (buf[i].valid) { sT += buf[i].t; sH += buf[i].h; cnt++; }
  }
  if (cnt == 0) { avgT = NAN; avgH = NAN; return; }
  avgT = sT / cnt;
  avgH = sH / cnt;
}

/*
  Save current relay states to EEPROM, for restoration after reset/power loss.
*/
void saveStatesToEEPROM() {
  byte states = 0;
  if (ssrHumState)  states |= 0x01;
  if (ssrFan1State) states |= 0x02;
  if (ssrFan2State) states |= 0x04;
  EEPROM.update(EEPROM_ADDR_FLAG, 0xAA);
  EEPROM.update(EEPROM_ADDR_STATES, states);
}

/*
  Periodically clear EEPROM state (minimizes write wear).
*/
void clearEepromIfNeeded() {
  if (millis() - lastEepromClearMs >= EEPROM_CLEAR_INTERVAL_MS) {
    if (EEPROM.read(EEPROM_ADDR_FLAG) != 0 || EEPROM.read(EEPROM_ADDR_STATES) != 0) {
      EEPROM.update(EEPROM_ADDR_FLAG, 0);
      EEPROM.update(EEPROM_ADDR_STATES, 0);
    }
    lastEepromClearMs = millis();
  }
}

/*
  Restore saved relay states from EEPROM and re-enable with safe staggering.
*/
void restoreStatesFromEEPROMWithStagger() {
  if (EEPROM.read(EEPROM_ADDR_FLAG) == 0) return;
  byte states = EEPROM.read(EEPROM_ADDR_STATES);

  bool wantHum = (states & 0x01) != 0;
  bool wantF1  = (states & 0x02) != 0;
  bool wantF2  = (states & 0x04) != 0;

  digitalWrite(SSR_HUM_PIN, HIGH);
  digitalWrite(SSR_FAN1_PIN, HIGH);
  digitalWrite(SSR_FAN2_PIN, HIGH);
  ssrHumState = ssrFan1State = ssrFan2State = false;
  delay(150);

  if (wantHum) {
    digitalWrite(SSR_HUM_PIN, LOW);
    ssrHumState = true;
    lastSSRChangeMillis = millis();
    delay(500);
  }
  if (wantF1) {
    while (millis() - lastSSRChangeMillis < SSR_STAGGER_MS) delay(20);
    digitalWrite(SSR_FAN1_PIN, LOW);
    ssrFan1State = true;
    lastSSRChangeMillis = millis();
    delay(500);
  }
  if (wantF2) {
    while (millis() - lastSSRChangeMillis < SSR_STAGGER_MS) delay(20);
    digitalWrite(SSR_FAN2_PIN, LOW);
    ssrFan2State = true;
    lastSSRChangeMillis = millis();
    delay(500);
  }
}

/*
  Safe setter for SSR (relay) outputs, enforces minimum stagger of 30s between toggles.
  SSR outputs are active LOW.
*/
void setSSR(uint8_t pin, bool &stateVar, bool on) {
  if (stateVar == on) return; // Already in desired state
  if (millis() - lastSSRChangeMillis < SSR_STAGGER_MS) return; // Enforce stagger
  digitalWrite(pin, on ? LOW : HIGH); // active LOW relay logic
  stateVar = on;
  lastSSRChangeMillis = millis();
  saveStatesToEEPROM();
}

/*
  Returns whether humidifier/Fan1 are permitted ON in current synthetic day window.
  OFF windows are 02:00–04:00, 10:00–12:00, 18:00–20:00; repeats every 24h uptime.
*/
bool isHumAllowedBySyntheticClock() {
  unsigned long sod = secondsOfSyntheticDay();
  unsigned long minutes = sod / 60UL;
  // 8h repeating window, first 2h are OFF starting at 02:00.
  int offset = (int)((minutes - 120UL + 1440UL) % 480UL); // 480 min = 8h
  return (offset >= 120);
}

// ===== I2C Protocol (Pi only sends "REQ" for telemetry) =====
void onReceiveFromMaster(int howMany) {
  // Buffers the incoming message; command is expected to be "REQ"
  char b[8];
  int n = 0;
  while (Wire.available() && n < (int)sizeof(b) - 1) {
    b[n++] = (char)Wire.read();
  }
  b[n] = '\0';
  if (n == 0) return;

  // Trim tail newline(s)
  while (n > 0 && (b[n-1] == '\n' || b[n-1] == '\r')) { b[--n] = 0; }

  // Only handle telemetry request "REQ"
  if (n == 3 && b[0]=='R' && b[1]=='E' && b[2]=='Q') {
    float ambT, ambH, z2T, z2H, z3T, z3H;
    computeAvg(ambBuf, ambT, ambH);
    computeAvg(z2Buf, z2T, z2H);
    computeAvg(z3Buf, z3T, z3H);

    float zonesT = NAN, zonesH = NAN;
    if (!isnan(z2T) && !isnan(z3T)) zonesT = 0.5f * (z2T + z3T);
    if (!isnan(z2H) && !isnan(z3H)) zonesH = 0.5f * (z2H + z3H);

    float tempDiff = (!isnan(zonesT) && !isnan(ambT)) ? (zonesT - ambT) : -999.0f;
    float humDiff  = (!isnan(zonesH) && !isnan(ambH)) ? (zonesH - ambH) : -999.0f;

    // 1-minute approx rate from last two samples in zones 2 & 3
    float ratePerMin = NAN;
    int idxLatest = (sampleIndex - 1 + SAMPLES_7MIN) % SAMPLES_7MIN;
    int idxPrev   = (idxLatest - 1 + SAMPLES_7MIN) % SAMPLES_7MIN;
    if (z2Buf[idxLatest].valid && z3Buf[idxLatest].valid && z2Buf[idxPrev].valid && z3Buf[idxPrev].valid) {
      float t1 = 0.5f * (z2Buf[idxLatest].t + z3Buf[idxLatest].t);
      float t0 = 0.5f * (z2Buf[idxPrev].t   + z3Buf[idxPrev].t);
      ratePerMin = t1 - t0;
    }

    unsigned long humRunMs = humAccumRunMs + (humOnStartMs ? (millis() - humOnStartMs) : 0UL);
    float humRunH = humRunMs / 3600000.0f;

    // Compose telemetry. "ts" is seconds since boot as a string
    char ts[16];
    ultoa(secondsSinceBoot(), ts, 10);

    txLen = snprintf(txBuffer, sizeof(txBuffer),
      "{\"ts\":\"%s\",\"zone1\":{\"t\":%.2f,\"h\":%.2f},\"zone2\":{\"t\":%.2f,\"h\":%.2f},\"zone3\":{\"t\":%.2f,\"h\":%.2f},"
      "\"zonesAvg\":{\"t\":%.2f,\"h\":%.2f},\"tempDiff\":%.2f,\"humDiff\":%.2f,"
      "\"ratePerMin\":%.3f,\"fan1\":%d,\"fan2\":%d,\"humid\":%d,\"humRunH\":%.3f}\n",
      ts,
      isnan(ambT)?-999.0f:ambT, isnan(ambH)?-999.0f:ambH,
      isnan(z2T)?-999.0f:z2T, isnan(z2H)?-999.0f:z2H,
      isnan(z3T)?-999.0f:z3T, isnan(z3H)?-999.0f:z3H,
      isnan(zonesT)?-999.0f:zonesT, isnan(zonesH)?-999.0f:zonesH,
      tempDiff, humDiff,
      isnan(ratePerMin)?0.0f:ratePerMin,
      ssrFan1State?1:0, ssrFan2State?1:0, ssrHumState?1:0, humRunH
    );
    if (txLen < 0) txLen = 0;
    if (txLen > (int)sizeof(txBuffer)) txLen = sizeof(txBuffer);
    txIndex = 0;
    txReady = true;
  }
}

/*
  Responds to I2C master requests by chunking out telemetry buffer.
*/
void onRequestToMaster() {
  if (!txReady || txIndex >= txLen) { Wire.write((const uint8_t*)"", 0); return; }
  const int CHUNK = 28;
  int remain = txLen - txIndex;
  int toSend = (remain > CHUNK) ? CHUNK : remain;
  Wire.write((const uint8_t*)(txBuffer + txIndex), (size_t)toSend);
  txIndex += toSend;
  if (txIndex >= txLen) txReady = false;
}

// ===== Arduino Setup/Loop =====
void setup() {
  Serial.begin(115200);
  bootMillis = millis();

  // Configure SSR outputs as active LOW, start with all OFF
  pinMode(SSR_HUM_PIN, OUTPUT);
  pinMode(SSR_FAN1_PIN, OUTPUT);
  pinMode(SSR_FAN2_PIN, OUTPUT);
  digitalWrite(SSR_HUM_PIN, HIGH);
  digitalWrite(SSR_FAN1_PIN, HIGH);
  digitalWrite(SSR_FAN2_PIN, HIGH);

  // Initialize DHT sensors
  dht1.begin();
  dht2.begin();
  dht3.begin();

  // Invalidate all sample buffers
  for (int i = 0; i < SAMPLES_7MIN; i++) {
    ambBuf[i].valid = false;
    z2Buf[i].valid = false;
    z3Buf[i].valid = false;
  }

  Wire.begin(I2C_ADDR);
  Wire.onReceive(onReceiveFromMaster);
  Wire.onRequest(onRequestToMaster);

  restoreStatesFromEEPROMWithStagger();

  lastSampleMillis = millis();
  lastEepromClearMs = millis();

  Serial.println(F("Controller ready (No Wall Clock). Day starts at 00:00 at boot."));
}

/*
  Control loop:
    - Reads sensors, updates rolling averages.
    - Checks humidity/temp control rules.
    - Manages SSR outputs and timing.
    - Handles forced off/timers/EEPROM maintenance.
    - Prints concise status to serial every sampling interval.
*/
void loop() {
  unsigned long nowMs = millis();

  // Sampling block: every SAMPLE_INTERVAL_MS
  if (nowMs - lastSampleMillis >= SAMPLE_INTERVAL_MS) {
    lastSampleMillis += SAMPLE_INTERVAL_MS;

    // Read sensors for all zones
    float t1 = dht1.readTemperature();
    float h1 = dht1.readHumidity();
    float t2 = dht2.readTemperature();
    float h2 = dht2.readHumidity();
    float t3 = dht3.readTemperature();
    float h3 = dht3.readHumidity();

    // Warn if sensor errors are detected
    if (isnan(t1) || isnan(h1)) Serial.println(F("[WARN] Zone1 DHT read failed"));
    if (isnan(t2) || isnan(h2)) Serial.println(F("[WARN] Zone2 DHT read failed"));
    if (isnan(t3) || isnan(h3)) Serial.println(F("[WARN] Zone3 DHT read failed"));

    // Push samples to respective rolling buffers
    pushSample(ambBuf, t1, h1);
    pushSample(z2Buf, t2, h2);
    pushSample(z3Buf, t3, h3);
    sampleIndex = (sampleIndex + 1) % SAMPLES_7MIN;

    // Compute 7-minute rolling averages for each zone
    float ambT, ambH, z2T, z2H, z3T, z3H;
    computeAvg(ambBuf, ambT, ambH);
    computeAvg(z2Buf, z2T, z2H);
    computeAvg(z3Buf, z3T, z3H);

    // Compute zone average temp & humidity (zone2 and zone3)
    float zonesT = NAN, zonesH = NAN;
    if (!isnan(z2T) && !isnan(z3T)) zonesT = 0.5f * (z2T + z3T);
    if (!isnan(z2H) && !isnan(z3H)) zonesH = 0.5f * (z2H + z3H);

    // Compute 1-minute temp change rate (degC/min)
    float ratePerMin = NAN;
    int idxLatest = (sampleIndex - 1 + SAMPLES_7MIN) % SAMPLES_7MIN;
    int idxPrev   = (idxLatest - 1 + SAMPLES_7MIN) % SAMPLES_7MIN;
    if (z2Buf[idxLatest].valid && z3Buf[idxLatest].valid && z2Buf[idxPrev].valid && z3Buf[idxPrev].valid) {
      float t1m = 0.5f * (z2Buf[idxLatest].t + z3Buf[idxLatest].t);
      float t0m = 0.5f * (z2Buf[idxPrev].t   + z3Buf[idxPrev].t);
      ratePerMin = t1m - t0m;
    }

    /*
      Humidifier/Fan1 scheduling and SSR state logic
      ---------------------------------------------------
      humWindow: true = ON window for humidifier and fan1 (outside synthetic OFF)
      Forced OFF if humidifier runs 6h continuously (forces 2h OFF cooldown).
      Additional basic hysteresis logic for humidity request.
    */
    bool humWindow = isHumAllowedBySyntheticClock();
    bool humForcedByMax = (humAccumRunMs + (humOnStartMs ? (nowMs - humOnStartMs) : 0UL) >= HUM_MAX_CONT_MS);

    // Handle forced off for humidifier after max continuous runtime
    if (humForcedByMax && !humInForcedOff) {
      humInForcedOff = true;
      humForcedOffStartMs = nowMs;
      setSSR(SSR_HUM_PIN, ssrHumState, false);
    }
    if (humInForcedOff) {
      if (nowMs - humForcedOffStartMs >= HUM_FORCED_OFF_MS) {
        humInForcedOff = false;
        humAccumRunMs = 0;
      } else {
        humWindow = false;
      }
    }

    // Determine if humidifier needs ON based on humidity with hysteresis
    bool humNeed = false;
    if (!isnan(zonesH)) {
      if (zonesH < HUM_LOW) humNeed = true;
      else if (zonesH > HUM_HIGH) humNeed = false;
      else humNeed = ssrHumState;
    }

    bool ambientEmergency = (!isnan(ambT) && ambT >= AMBIENT_EMERGENCY);
    bool overrideTemp = (!isnan(zonesT) && !isnan(ambT) && zonesT >= ambT);

    // Fan1 now mirrors humidifier ON/OFF windows!
    bool shouldHum = false, shouldFan1 = false, shouldFan2 = false;

    /*
      MAIN SSR LOGIC:
      - Emergency/override forces fan1/fan2 ON; humidifier only if scheduled and needed.
      - Normal: humidifier and fan1 ON only during scheduled window.
    */
    if (ambientEmergency) {
      shouldHum = (humWindow && !humInForcedOff && humNeed);
      shouldFan1 = true;
      shouldFan2 = true;
    } else if (overrideTemp) {
      shouldHum = (humWindow && !humInForcedOff && humNeed);
      shouldFan1 = true;
      shouldFan2 = true;
    } else {
      shouldHum = (humWindow && !humInForcedOff && humNeed);
      shouldFan1 = humWindow;  // Mirrors humidifier window
      shouldFan2 = false;
    }

    // Never ON outside humWindow
    if (!humWindow) shouldHum = false;
    // If max continuous ON reached, never ON until forced OFF releases
    if (humOnStartMs && (nowMs - humOnStartMs + humAccumRunMs >= HUM_MAX_CONT_MS)) {
      shouldHum = false;
    }

    // --- SSR outputs (active LOW) ---
    setSSR(SSR_HUM_PIN, ssrHumState, shouldHum);
    setSSR(SSR_FAN1_PIN, ssrFan1State, shouldFan1);
    setSSR(SSR_FAN2_PIN, ssrFan2State, shouldFan2);

    // Track humidifier runtime (cover hysteresis transitions cleanly)
    if (!ssrHumState && humOnStartMs) {
      humAccumRunMs += (nowMs - humOnStartMs);
      humOnStartMs = 0;
    }
    if (ssrHumState && !humOnStartMs) humOnStartMs = nowMs;

    clearEepromIfNeeded();

    // Serial concise status for monitoring
    Serial.print(F("[t="));
    Serial.print(secondsSinceBoot());
    Serial.print(F("s] Z1("));
    Serial.print(ambT); Serial.print(F("C "));
    Serial.print(ambH); Serial.print(F("%)), Z2("));
    Serial.print(z2T); Serial.print(F("C "));
    Serial.print(z2H); Serial.print(F("%)), Z3("));
    Serial.print(z3T); Serial.print(F("C "));
    Serial.print(z3H); Serial.print(F("%)) | Avg("));
    Serial.print(zonesT); Serial.print(F("C "));
    Serial.print(zonesH); Serial.print(F("%)) | H:"));
    Serial.print(ssrHumState); Serial.print(F(" F1:"));
    Serial.print(ssrFan1State); Serial.print(F(" F2:"));
    Serial.print(ssrFan2State); Serial.println();
  }

  // Short delay for loop responsiveness
  delay(5);
}
