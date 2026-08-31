const int numPins = 6;                                   
const int pins[numPins] = {2, 3, 4, 5, 6, 7};           

int counter[numPins] = {0, 0, 0, 0, 0, 0};
int lastState[numPins] = {LOW, LOW, LOW, LOW, LOW, LOW};
unsigned long lastPressTime[numPins] = {0, 0, 0, 0, 0, 0};
const unsigned long cooldown = 200;

int strokesInWindow[numPins] = {0, 0, 0, 0, 0, 0};
int strokePerMinute[numPins] = {0, 0, 0, 0, 0, 0};
unsigned long windowStartTime[numPins] = {0, 0, 0, 0, 0, 0};
const unsigned long sampleWindow = 4000;

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < numPins; i++) {
    pinMode(pins[i], INPUT);   
    windowStartTime[i] = millis();
  }
}

void loop() {
  unsigned long now = millis();
  bool changed = false;

  for (int i = 0; i < numPins; i++) {
    int currentState = digitalRead(pins[i]);

    if (currentState == HIGH && lastState[i] == LOW && (now - lastPressTime[i] > cooldown)) {
      counter[i]++;
      strokesInWindow[i]++;
      lastPressTime[i] = now;
      changed = true;
    }
    lastState[i] = currentState;

    if (now - windowStartTime[i] >= sampleWindow) {
      strokePerMinute[i] = strokesInWindow[i] * 15;
      strokesInWindow[i] = 0;
      windowStartTime[i] = now;
      changed = true;
    }
  }

  if (changed) {
    printActiveJSON();
    delay(20);
  }
}

void printActiveJSON() {
  int activeCount = 0;
  for (int i = 0; i < numPins; i++) {
    if (counter[i] > 0) activeCount++;
  }
  if (activeCount == 0) return;

  Serial.print("{");
  int printed = 0;
  
  // RPM channel counter (starts at 1 for pin 6)
  int rpmChannel = 1;
  
  for (int i = 0; i < numPins; i++) {
    if (counter[i] > 0) {
      if (printed > 0) Serial.print(",");
      
      // ----- Counter key (always counter1 to counter6) -----
      Serial.print("\"counter");
      Serial.print(i + 1);
      Serial.print("\":");
      Serial.print(counter[i]);
      Serial.print(",");
      
      // ----- Rate key: spm for pins 2-5, rpm for pins 6-7 -----
      if (i < 4) {
        // Pins 2,3,4,5 (indices 0-3) → spm1 to spm4
        Serial.print("\"spm");
        Serial.print(i + 1);
        Serial.print("\":");
      } else {
        // Pins 6,7 (indices 4-5) → rpm1, rpm2
        Serial.print("\"rpm");
        Serial.print(rpmChannel);
        Serial.print("\":");
        rpmChannel++;
      }
      Serial.print(strokePerMinute[i]);
      
      printed++;
    }
  }
  Serial.println("}");
}