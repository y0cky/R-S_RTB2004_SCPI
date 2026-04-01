from RsInstrument import RsInstrument
import matplotlib.pyplot as plt
import numpy as np

# -----------------------------
# Verbindung
# -----------------------------
rtb = RsInstrument("TCPIP::192.168.1.13::INSTR", True, False)
rtb.visa_timeout = 20000
rtb.opc_timeout = 30000

# -----------------------------
# Mess-Setup
# -----------------------------
acq_time = 0.01  # 10 ms

rtb.write_str("CHAN1:STAT ON")
rtb.write_str("CHAN1:SCAL 0.5")
rtb.write_str("CHAN1:COUP DCL")
rtb.write_str(f"TIM:ACQT {acq_time}")
rtb.write_str("TRIG:A:MODE AUTO")

# -----------------------------
# Single Shot
# -----------------------------
rtb.write_str("SING")
rtb.query_opc()

# -----------------------------
# Waveform auslesen (ASCII, stabil)
# -----------------------------
trace = rtb.query_bin_or_ascii_float_list(
    "FORM ASC;:CHAN1:DATA?"
)

rtb.close()

# -----------------------------
# Zeitachse berechnen
# -----------------------------
trace = np.array(trace)
n = len(trace)
dt = acq_time / n
time_axis = np.arange(n) * dt

# -----------------------------
# Plot
# -----------------------------
plt.figure(figsize=(10, 4))
plt.plot(time_axis, trace, linewidth=1)

plt.xlabel("Time [s]")
plt.ylabel("Voltage [V]")
plt.title("RTB2004 – CH1 Waveform")
plt.grid(True)

plt.tight_layout()
plt.show()