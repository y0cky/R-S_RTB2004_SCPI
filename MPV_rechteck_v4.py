import customtkinter as ctk
import tkinter.ttk as ttk
import threading
import time
import csv
import numpy as np
from RsInstrument import RsInstrument
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ============================================================
# Konfiguration
# ============================================================
RTB_IP = "192.168.1.13"
CSV_FILE = "ableitsystem_bewertung.csv"
SETTLING_TIME = 0.05
HF_FACTOR = 5   # HF-Band beginnt bei 5 * Grundfrequenz

# ============================================================
# Analysefunktionen
# ============================================================
def fft_peak(signal, fs):
    sig = signal - np.mean(signal)
    fft = np.fft.rfft(sig)
    mag = np.abs(fft) * (2.0 / len(sig))
    mag[0] = 0
    freq = np.fft.rfftfreq(len(sig), 1 / fs)
    idx = np.argmax(mag)
    return freq[idx], mag[idx]


def hf_band_energy(signal, fs, f_start):
    sig = signal - np.mean(signal)
    fft = np.fft.rfft(sig)
    mag = np.abs(fft) * (2.0 / len(sig))
    freq = np.fft.rfftfreq(len(sig), 1 / fs)
    return np.sum(mag[freq >= f_start] ** 2)


def time_domain_peak(signal):
    return np.max(np.abs(signal))


# ============================================================
# GUI
# ============================================================
class AbleitsystemGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("RTB2004 – Ableitsystem Prüfstand")
        self.geometry("1450x900")

        self.rtb = None
        self.abort_event = threading.Event()
        self.wait_for_dut_event = threading.Event()

        self._init_data()
        self._build_gui()

    # --------------------------------------------------------
    def _init_data(self):
        self.freqs = []
        self.baseline = {}
        self.u_meas = []
        self.u_corr = []
        self.atten_dut_db = []
        self.hf_energy = []
        self.time_peaks = []

    # --------------------------------------------------------
    def _build_gui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=10, pady=5)

        def add_entry(txt, default):
            ctk.CTkLabel(top, text=txt).pack(side="left", padx=4)
            e = ctk.CTkEntry(top, width=80)
            e.insert(0, str(default))
            e.pack(side="left")
            return e

        self.e_fstart  = add_entry("f Start [Hz]", 500)
        self.e_fstop   = add_entry("f Stop [Hz]", 20000)
        self.e_fstep   = add_entry("f Step [Hz]", 500)
        self.e_vpp     = add_entry("Vpp", 1.0)
        self.e_periods = add_entry("N Perioden", 40)
        self.e_avg     = add_entry("Mittelungen", 5)

        self.gen_mode = ctk.StringVar(value="Sinus")
        ctk.CTkOptionMenu(
            top, variable=self.gen_mode,
            values=["Sinus", "Rechteck 50 %"]
        ).pack(side="left", padx=10)

        ctk.CTkButton(top, text="Baseline starten",
                      command=self.start_baseline).pack(side="left", padx=5)

        ctk.CTkButton(top, text="DUT messen",
                      command=self.start_dut).pack(side="left", padx=5)

        ctk.CTkButton(top, text="Abbrechen",
                      fg_color="red",
                      command=self.abort).pack(side="left", padx=5)

        # Status / Progress
        self.phase_label = ctk.CTkLabel(
            self, text="Phase: idle",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.phase_label.pack(pady=(5, 0))

        self.detail_label = ctk.CTkLabel(self, text="")
        self.detail_label.pack()

        self.progress_bar = ctk.CTkProgressBar(self, width=900)
        self.progress_bar.set(0)
        self.progress_bar.pack(pady=5)

        # Hauptbereich
        main = ctk.CTkFrame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # Tabelle
        self.tree = ttk.Treeview(
            main,
            columns=("f","Ub","Um","Uc","dB","HF","Peak"),
            show="headings", height=22
        )

        headers = (
            "f [Hz]",
            "Baseline [V]",
            "mit DUT [V]",
            "korrigiert",
            "DUT [dB]",
            "HF‑Energie [V²]",
            "Zeit‑Peak [V]"
        )

        for col, txt in zip(self.tree["columns"], headers):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=150)

        self.tree.pack(side="left", fill="y")

        # Plot
        plot_frame = ctk.CTkFrame(main)
        plot_frame.pack(side="right", fill="both", expand=True)

        self.plot_mode = ctk.StringVar(value="DUT Dämpfung")
        self.plot_mode.trace_add("write", lambda *_: self.update_plot())

        ctk.CTkOptionMenu(
            plot_frame,
            variable=self.plot_mode,
            values=[
                "Baseline",
                "mit Prüfling",
                "DUT (korrigiert)",
                "DUT Dämpfung",
                "HF‑Energie",
                "Zeit‑Peak"
            ]
        ).pack(anchor="ne", padx=5, pady=5)

        self.fig, self.ax = plt.subplots(figsize=(6, 4))
        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # --------------------------------------------------------
    def update_status(self, phase, i=0, total=0, f=None):
        self.after(0, lambda:
            self.phase_label.configure(text=f"Phase: {phase}")
        )
        if f is not None:
            self.after(0, lambda:
                self.detail_label.configure(
                    text=f"f = {int(f)} Hz ({i}/{total})"
                )
            )
            self.after(0, lambda:
                self.progress_bar.set(i / total)
            )

    # --------------------------------------------------------
    def abort(self):
        self.abort_event.set()
        self.update_status("Abbruch angefordert")

    # --------------------------------------------------------
    def start_baseline(self):
        self.abort_event.clear()
        self._init_data()
        self.tree.delete(*self.tree.get_children())
        threading.Thread(
            target=self.measurement_thread,
            args=(True,),
            daemon=True
        ).start()

    # --------------------------------------------------------
    def start_dut(self):
        if not self.baseline:
            self.update_status("Fehler")
            self.after(0, lambda:
                self.detail_label.configure(
                    text="Bitte zuerst Baseline messen!")
            )
            return

        self.abort_event.clear()
        threading.Thread(
            target=self.measurement_thread,
            args=(False,),
            daemon=True
        ).start()

    # --------------------------------------------------------
    def configure_generator(self, f):
        self.rtb.write_str(f"WGEN:FREQ {f}")
        self.rtb.write_str(f"WGEN:VOLT {float(self.e_vpp.get())}")
        self.rtb.write_str("WGEN:VOLT:OFFS 0")
        self.rtb.write_str(
            "WGEN:FUNC SIN"
            if self.gen_mode.get() == "Sinus"
            else "WGEN:FUNC SQU"
        )

    # --------------------------------------------------------
    def measurement_thread(self, baseline_only):
        try:
            self.rtb = RsInstrument(
                f"TCPIP::{RTB_IP}::INSTR", True, False
            )

            self.rtb.write_str("WGEN:OUTP ON")
            self.rtb.write_str("CHAN1:STAT ON")
            self.rtb.write_str("TRIG:A:MODE AUTO")

            freqs = np.arange(
                float(self.e_fstart.get()),
                float(self.e_fstop.get()) + float(self.e_fstep.get()),
                float(self.e_fstep.get())
            )
            total = len(freqs)

            NP = int(self.e_periods.get())
            NA = int(self.e_avg.get())

            for i, f in enumerate(freqs, start=1):
                if self.abort_event.is_set():
                    break

                phase = "Baseline" if baseline_only else "DUT"
                self.update_status(phase, i, total, f)

                acq_time = NP / f
                self.rtb.write_str(f"TIM:ACQT {acq_time}")
                self.configure_generator(f)
                time.sleep(SETTLING_TIME)

                fft_vals = []
                hf_vals = []
                peak_vals = []

                for _ in range(NA):
                    self.rtb.write_str("SING")
                    self.rtb.query_opc()

                    u = np.array(
                        self.rtb.query_bin_or_ascii_float_list(
                            "FORM ASC;:CHAN1:DATA?"
                        )
                    )
                    fs = len(u) / acq_time

                    _, up = fft_peak(u, fs)
                    fft_vals.append(up)
                    hf_vals.append(hf_band_energy(u, fs, HF_FACTOR * f))
                    peak_vals.append(time_domain_peak(u))

                fft_mean = np.mean(fft_vals)
                hf_mean = np.mean(hf_vals)
                peak_mean = np.mean(peak_vals)

                if baseline_only:
                    self.baseline[f] = fft_mean
                else:
                    corr = fft_mean / self.baseline[f]
                    db = 20 * np.log10(corr)

                    self.freqs.append(f)
                    self.u_meas.append(fft_mean)
                    self.u_corr.append(corr)
                    self.atten_dut_db.append(db)
                    self.hf_energy.append(hf_mean)
                    self.time_peaks.append(peak_mean)

                    self.after(
                        0,
                        lambda f=f,b=self.baseline[f],m=fft_mean,
                               c=corr,d=db,hf=hf_mean,p=peak_mean:
                        self.tree.insert(
                            "", "end",
                            values=(
                                int(f),
                                f"{b:.3e}",
                                f"{m:.3e}",
                                f"{c:.4f}",
                                f"{d:.1f}",
                                f"{hf:.3e}",
                                f"{p:.3e}"
                            )
                        )
                    )
                    self.after(0, self.update_plot)

            if not baseline_only:
                with open(CSV_FILE, "w", newline="") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow([
                        "f_Hz","U_baseline","U_meas",
                        "U_corr","DUT_dB",
                        "HF_Energy","Time_Peak"
                    ])
                    for i, f in enumerate(self.freqs):
                        w.writerow([
                            f,
                            self.baseline[f],
                            self.u_meas[i],
                            self.u_corr[i],
                            self.atten_dut_db[i],
                            self.hf_energy[i],
                            self.time_peaks[i]
                        ])

            self.update_status("fertig")

        finally:
            if self.rtb:
                self.rtb.write_str("WGEN:OUTP OFF")
                self.rtb.close()

    # --------------------------------------------------------
    def update_plot(self):
        self.ax.clear()

        if not self.freqs:
            self.canvas.draw_idle()
            return

        mode = self.plot_mode.get()

        if mode == "Baseline":
            y = [self.baseline[f] for f in self.freqs]
        elif mode == "mit Prüfling":
            y = self.u_meas
        elif mode == "DUT (korrigiert)":
            y = self.u_corr
        elif mode == "DUT Dämpfung":
            y = self.atten_dut_db
        elif mode == "HF‑Energie":
            y = self.hf_energy
        else:
            y = self.time_peaks

        self.ax.plot(self.freqs, y, marker="o")
        self.ax.set_xlabel("Frequenz [Hz]")
        self.ax.grid(True)
        self.canvas.draw_idle()


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    AbleitsystemGUI().mainloop()