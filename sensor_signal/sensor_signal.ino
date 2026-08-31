// ============================================================
//  16‑Channel Analog Sensor Reader (A0 – A15)
//  JSON: {"sensor1":V, "sensor2":V, ..., "sensor16":V}
// ============================================================

const int sensorPins[] = {A0, A1, A2, A3, A4, A5, A6, A7, A8, A9, A10, A11, A12, A13, A14, A15};
const int numSensors = sizeof(sensorPins) / sizeof(sensorPins[0]);

const int NUM_SAMPLES = 10;   // number of samples to average
const float VOLTAGE_REF = 5.0;
const int ADC_MAX = 1023;

// ------------------------------------------------------------------
float readAverageVoltage(int pin) {
  long sum = 0;
  for (int i = 0; i < NUM_SAMPLES; i++) {
    sum += analogRead(pin);
    delayMicroseconds(100);
  }
  float avgRaw = sum / (float)NUM_SAMPLES;
  return avgRaw * (VOLTAGE_REF / ADC_MAX);
}

// ------------------------------------------------------------------
void setup() {
  Serial.begin(9600);
  for (int i = 0; i < numSensors; i++) {
    pinMode(sensorPins[i], INPUT);
  }
}

// ------------------------------------------------------------------
void loop() {
  float voltages[numSensors];
  for (int i = 0; i < numSensors; i++) {
    voltages[i] = readAverageVoltage(sensorPins[i]);
  }

  // Print JSON
  Serial.print("{");
  for (int i = 0; i < numSensors; i++) {
    Serial.print("\"sensor");
    Serial.print(i + 1);           // sensor1, sensor2, ..., sensor16
    Serial.print("\":");
    Serial.print(voltages[i], 3);   // 3 decimal places
    if (i < numSensors - 1) {
      Serial.print(",");
    }
  }
  Serial.println("}");

  delay(1500);   // wait 1.5 seconds before next reading
}
