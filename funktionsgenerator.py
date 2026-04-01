from RsInstrument import RsInstrument
import time

# -----------------------------
# Verbindung
# -----------------------------
RTB_IP = "192.168.1.13"   # anpassen
rtb = RsInstrument(f"TCPIP::{RTB_IP}::INSTR", True, False)

rtb.visa_timeout = 5000
rtb.opc_timeout = 5000

print("Verbunden mit:")
print(rtb.idn_string)

# -----------------------------
# Funktionsgenerator Setup
# -----------------------------
print("Aktiviere Funktionsgenerator")

rtb.write_str("WGEN:OUTP OFF")          # Sicherer Start
rtb.write_str("WGEN:FUNC SIN")          # Sinus
rtb.write_str("WGEN:FREQ 1000")         # 1 kHz
rtb.write_str("WGEN:VOLT 1.0")          # 1 Vpp
rtb.write_str("WGEN:VOLT:OFFS 0.0")     # <-- RICHTIGER Offset-Befehl

rtb.write_str("WGEN:OUTP ON")           # Generator EIN

print("Signal aktiv: Sinus, 1 kHz, 1 Vpp, 0 V Offset")

# -----------------------------
# Testlauf
# -----------------------------
time.sleep(5)

# -----------------------------
# Generator AUS
# -----------------------------
print("Schalte Generator aus")
rtb.write_str("WGEN:OUTP OFF")

rtb.close()
print("Fertig")