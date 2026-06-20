// ----- 4-Channel SSR Control (Active LOW) -----
// Each relay turns ON for 5 seconds, one after another.
// After all 4 are done, wait 30 seconds before repeating.
//
// Connections (example):
// Relay CH1 -> Arduino pin 2
// Relay CH2 -> Arduino pin 3
// Relay CH3 -> Arduino pin 4
// Relay CH4 -> Arduino pin 5
//
// NOTE: Active LOW SSR -> LOW = ON, HIGH = OFF

#define RELAY_COUNT 4
int relays[RELAY_COUNT] = {2, 3, 4, 5};

unsigned long relayOnTime = 5000;     // 5 seconds per relay
unsigned long waitAfterCycle = 30000; // 30 seconds between cycles

void setup() {
  // Initialize relay pins as OUTPUT
  for (int i = 0; i < RELAY_COUNT; i++) {
    pinMode(relays[i], OUTPUT);
    digitalWrite(relays[i], HIGH); // Make sure all OFF initially
  }

  Serial.begin(9600);
  Serial.println("Active LOW 4-Channel SSR Control Started");
}

void loop() {
  Serial.println("Starting Relay Cycle...");

  // Turn on each relay one by one for 5 seconds
  for (int i = 0; i < RELAY_COUNT; i++) {
    Serial.print("Relay ");
    Serial.print(i + 1);
    Serial.println(" ON");

    digitalWrite(relays[i], LOW);   // Turn ON relay (active LOW)
    delay(relayOnTime);             // Keep ON for 5s
    digitalWrite(relays[i], HIGH);  // Turn OFF relay

    Serial.print("Relay ");
    Serial.print(i + 1);
    Serial.println(" OFF");
  }

  // Wait 30 seconds before starting the next cycle
  Serial.println("Cycle complete. Waiting 30 seconds...");
  delay(waitAfterCycle);
}