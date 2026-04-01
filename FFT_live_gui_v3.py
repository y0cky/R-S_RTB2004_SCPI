import customtkinter as ctk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from RsInstrument import RsInstrument
import threading
import queue
import csv
from datetime import datetime

# =============================
# Parameter
# =============================
DEFAULT_IP = "192.168.1.13"
ACQ_TIME = 0.01                 # 10 ms
CSV_FILE = "live_fft_harmonics.csv"

MAX_HARMONICS = 8               # H1 ... H8
FREQ_TOLERANCE = 0.02           # ±2 %
MIN_PEAK_DB = -80               # Rauschschwelle


# =============================
# GUI Klasse
# =============================
class RTB2004GUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("RTB2004 – Live Time + FFT + Harmonics")
        self.geometry("1250x700")

        self.running = False
        self.data_queue = queue.Queue(maxsize=2)
        self.rtb = None

        self.csv_file = None
        self.csv_writer = None

        self.harmonic_labels = []

        self._build_gui()

    # -------------------------
    # GUI Aufbau
    # -------------------------
    def _build_gui(self):

        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=10, pady=5)

        self.ip_entry = ctk.CTkEntry(top, width=200)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.pack(side="left", padx=5)

        self.btn_start = ctk.CTkButton(top, text="Start", command=self.start)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ctk.CTkButton(
            top, text="Stop", command=self.stop, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=5)

        self.status_label = ctk.CTkLabel(top, text="Status: idle")
        self.status_label.pack(side="right", padx=10)

        self.peak_label = ctk.CTkLabel(top, text="H1: --- Hz")
        self.peak_label.pack(side="right", padx=20)

        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.fig, (self.ax_time, self.ax_fft) = plt.subplots(1, 2, figsize=(11, 4))

        self.line_time, = self.ax_time.plot([], [])
        self.line_fft, = self.ax_fft.plot([], [])

        self.ax_time.set_title("Zeitdomäne")
        self.ax_time.set_xlabel("Time [s]")
        self.ax_time.set_ylabel("Voltage [V]")
        self.ax_time.grid(True)

        self.ax_fft.set_title("FFT + Harmonische")
        self.ax_fft.set_xlabel("Frequency [Hz]")
        self.ax_fft.set_ylabel("Magnitude [dB]")
        self.ax_fft.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.after(50, self.update_gui)

    # -------------------------
    # Start / Stop
    # -------------------------
    def start(self):
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_label.configure(text="Status: running")

        self.csv_file = open(CSV_FILE, "w", newline="")
        self.csv_writer = csv.writer(self.csv_file, delimiter=";")
        self.csv_writer.writerow([
            "timestamp",
            "harmonic",
            "frequency_Hz",
            "level_dB",
            "rms_V",
            "vpp_V"
        ])

        threading.Thread(target=self.measurement_thread, daemon=True).start()

    def stop(self):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_label.configure(text="Status: stopped")

        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None

    # -------------------------
    # Measurement Thread
    # -------------------------
    def measurement_thread(self):
        try:
            ip = self.ip_entry.get().strip()
            self.rtb = RsInstrument(f"TCPIP::{ip}::INSTR", True, False)
            self.rtb.visa_timeout = 20000
            self.rtb.opc_timeout = 30000

            self.rtb.write_str("CHAN1:STAT ON")
            self.rtb.write_str("CHAN1:SCAL 0.5")
            self.rtb.write_str("CHAN1:COUP DCL")
            self.rtb.write_str(f"TIM:ACQT {ACQ_TIME}")
            self.rtb.write_str("TRIG:A:MODE AUTO")

            while self.running:
                self.rtb.write_str("SING")
                self.rtb.query_opc()

                trace = self.rtb.query_bin_or_ascii_float_list(
                    "FORM ASC;:CHAN1:DATA?"
                )

                if not self.data_queue.full():
                    self.data_queue.put(np.array(trace))

        finally:
            if self.rtb:
                self.rtb.close()

    # -------------------------
    # GUI Update (Main Thread)
    # -------------------------
    def update_gui(self):
        if not self.data_queue.empty():
            trace = self.data_queue.get()

            n = len(trace)
            t = np.linspace(0, ACQ_TIME, n, endpoint=False)

            # ===== Zeitplot =====
            self.line_time.set_data(t, trace)
            self.ax_time.set_xlim(0, ACQ_TIME)
            self.ax_time.set_ylim(trace.min()*1.2, trace.max()*1.2)

            # ===== FFT =====
            dc_removed = trace - np.mean(trace)
            window = np.hanning(n)
            fft = np.fft.rfft(dc_removed * window)

            fs = n / ACQ_TIME
            freq = np.fft.rfftfreq(n, 1/fs)
            mag = 20*np.log10(np.abs(fft) + 1e-12)

            self.line_fft.set_data(freq, mag)
            self.ax_fft.set_xlim(0, fs/2)
            self.ax_fft.set_ylim(mag.max()-80, mag.max()+5)

            # Alte Harmonische entfernen
            for lbl in self.harmonic_labels:
                lbl.remove()
            self.harmonic_labels.clear()

            # ===== Harmonische erkennen =====
            mag_no_dc = mag.copy()
            mag_no_dc[0] = -np.inf

            h1_idx = np.argmax(mag_no_dc)
            f0 = freq[h1_idx]

            harmonics = [(1, freq[h1_idx], mag[h1_idx])]

            for k in range(2, MAX_HARMONICS + 1):
                target = k * f0
                if target > freq[-1]:
                    break

                fmin = target * (1 - FREQ_TOLERANCE)
                fmax = target * (1 + FREQ_TOLERANCE)

                idx = np.where((freq >= fmin) & (freq <= fmax))[0]
                if len(idx) == 0:
                    continue

                p = idx[np.argmax(mag[idx])]
                if mag[p] > MIN_PEAK_DB:
                    harmonics.append((k, freq[p], mag[p]))

            # ===== Marker + CSV =====
            rms = np.sqrt(np.mean(trace**2))
            vpp = trace.max() - trace.min()

            for order, fh, mh in harmonics:
                label = self.ax_fft.text(
                    fh, mh, f"H{order}",
                    color="yellow", fontsize=9,
                    ha="center", va="bottom"
                )
                self.harmonic_labels.append(label)

                self.csv_writer.writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    f"H{order}",
                    f"{fh:.2f}",
                    f"{mh:.2f}",
                    f"{rms:.4f}",
                    f"{vpp:.4f}"
                ])

            self.peak_label.configure(text=f"H1: {f0:8.1f} Hz")
            self.canvas.draw_idle()

        self.after(50, self.update_gui)


# =============================
# Main
# =============================
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = RTB2004GUI()
    app.mainloop()
