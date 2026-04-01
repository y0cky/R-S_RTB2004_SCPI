import customtkinter as ctk
import threading
import time
from RsInstrument import RsInstrument

# =============================
# Konfiguration
# =============================
DEFAULT_IP = "192.168.1.13"
SWEEP_DELAY = 0.3  # Sekunden zwischen Schritten


# =============================
# GUI Klasse
# =============================
class FunctionGeneratorGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("RTB2004 – Funktionsgenerator")
        self.geometry("500x520")

        self.rtb = None
        self.running = False

        self._build_gui()

    # -------------------------
    # GUI Aufbau
    # -------------------------
    def _build_gui(self):

        # --- Verbindung ---
        frame_conn = ctk.CTkFrame(self)
        frame_conn.pack(fill="x", padx=10, pady=10)

        self.ip_entry = ctk.CTkEntry(frame_conn)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.pack(fill="x", padx=5, pady=5)

        self.btn_connect = ctk.CTkButton(frame_conn, text="Verbinden", command=self.connect)
        self.btn_connect.pack(fill="x", padx=5)

        self.status_label = ctk.CTkLabel(frame_conn, text="Status: nicht verbunden")
        self.status_label.pack(pady=5)

        # --- Generator Einstellungen ---
        frame_gen = ctk.CTkFrame(self)
        frame_gen.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(frame_gen, text="Wellenform").pack(anchor="w", padx=5)
        self.waveform_box = ctk.CTkComboBox(frame_gen, values=["SIN", "SQU", "RAMP"])
        self.waveform_box.set("SIN")
        self.waveform_box.pack(fill="x", padx=5, pady=5)

        ctk.CTkButton(frame_gen, text="Generator EIN", command=self.gen_on).pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(frame_gen, text="Generator AUS", command=self.gen_off).pack(fill="x", padx=5, pady=5)

        # --- Frequenz Sweep ---
        frame_freq = ctk.CTkFrame(self)
        frame_freq.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(frame_freq, text="Frequenz Sweep (Hz)").pack(anchor="w", padx=5)

        self.freq_start = self._entry(frame_freq, "Start", "100")
        self.freq_stop = self._entry(frame_freq, "Stop", "10000")
        self.freq_step = self._entry(frame_freq, "Step", "500")

        ctk.CTkButton(frame_freq, text="Frequenz Sweep starten",
                      command=lambda: self.start_sweep("freq")).pack(fill="x", padx=5, pady=5)

        # --- Amplituden Sweep ---
        frame_amp = ctk.CTkFrame(self)
        frame_amp.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(frame_amp, text="Amplitude Sweep (Vpp)").pack(anchor="w", padx=5)

        self.amp_start = self._entry(frame_amp, "Start", "0.5")
        self.amp_stop = self._entry(frame_amp, "Stop", "2.0")
        self.amp_step = self._entry(frame_amp, "Step", "0.25")

        ctk.CTkButton(frame_amp, text="Amplitude Sweep starten",
                      command=lambda: self.start_sweep("amp")).pack(fill="x", padx=5, pady=5)

    # -------------------------
    # Hilfsfunktionen
    # -------------------------
    def _entry(self, parent, label, default):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(frame, text=label).pack(side="left")
        e = ctk.CTkEntry(frame)
        e.insert(0, default)
        e.pack(side="right", fill="x", expand=True)
        return e

    # -------------------------
    # Verbindung
    # -------------------------
    def connect(self):
        ip = self.ip_entry.get().strip()
        try:
            self.rtb = RsInstrument(f"TCPIP::{ip}::INSTR", True, False)
            self.status_label.configure(text="Status: verbunden")
        except Exception as e:
            self.status_label.configure(text=f"Fehler: {e}")

    # -------------------------
    # Generator Steuerung
    # -------------------------
    def gen_on(self):
        if not self.rtb:
            return
        self.rtb.write_str(f"WGEN:FUNC {self.waveform_box.get()}")
        self.rtb.write_str("WGEN:OUTP ON")

    def gen_off(self):
        if self.rtb:
            self.rtb.write_str("WGEN:OUTP OFF")

    # -------------------------
    # Sweep Logik
    # -------------------------
    def start_sweep(self, mode):
        if not self.rtb:
            return
        threading.Thread(target=self.sweep_thread, args=(mode,), daemon=True).start()

    def sweep_thread(self, mode):
        try:
            if mode == "freq":
                start = float(self.freq_start.get())
                stop = float(self.freq_stop.get())
                step = float(self.freq_step.get())

                for f in self._frange(start, stop, step):
                    self.rtb.write_str(f"WGEN:FREQ {f}")
                    time.sleep(SWEEP_DELAY)

            elif mode == "amp":
                start = float(self.amp_start.get())
                stop = float(self.amp_stop.get())
                step = float(self.amp_step.get())

                for a in self._frange(start, stop, step):
                    self.rtb.write_str(f"WGEN:VOLT {a}")
                    time.sleep(SWEEP_DELAY)

        except Exception as e:
            print("Sweep Fehler:", e)

    @staticmethod
    def _frange(start, stop, step):
        while start <= stop:
            yield start
            start += step


# =============================
# MAIN
# =============================
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = FunctionGeneratorGUI()
    app.mainloop()