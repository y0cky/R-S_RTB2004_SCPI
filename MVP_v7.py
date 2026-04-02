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
U_MIN_VALID = 1e-6


# ============================================================
# Bewertung
# ============================================================
def bewertung_eta(eta):
    if eta >= 0.7:
        return "gut"
    elif eta >= 0.3:
        return "kritisch"
    else:
        return "schlecht"


# ============================================================
# FFT Peak – Scheitelwert
# ============================================================
def fft_peak(signal, fs):
    sig = signal - np.mean(signal)
    fft = np.fft.rfft(sig)
    mag = np.abs(fft) * (2.0 / len(sig))
    mag[0] = 0
    freq = np.fft.rfftfreq(len(sig), 1 / fs)
    idx = np.argmax(mag)
    return freq[idx], mag[idx]


# ============================================================
# GUI
# ============================================================
class AbleitsystemGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("RTB2004 – Ableitsystem Prüfstand")
        self.geometry("1250x720")

        self.wait_for_dut_event = threading.Event()
        self.running = False
        self.rtb = None

        self.freqs = []
        self.u_peaks = []
        self.i_peaks = []
        self.etas = []
        self.atten_dbs = []

        self._build_gui()

    # --------------------------------------------------------
    def _build_gui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=10, pady=5)

        def add_param(text, default, width=80):
            ctk.CTkLabel(top, text=text).pack(side="left", padx=4)
            e = ctk.CTkEntry(top, width=width)
            e.insert(0, str(default))
            e.pack(side="left")
            return e

        self.e_fstart   = add_param("f Start [Hz]", 500)
        self.e_fstop    = add_param("f Stop [Hz]", 20000)
        self.e_fstep    = add_param("f Step [Hz]", 500)
        self.e_vamp     = add_param("Vpp", 1.0)
        self.e_shunt    = add_param("Rshunt [Ω]", 10.0)
        self.e_periods  = add_param("N Perioden", 40)
        self.e_avg      = add_param("Mittelungen", 5)

        self.btn_start = ctk.CTkButton(top, text="Messung starten", command=self.start)
        self.btn_start.pack(side="left", padx=10)

        self.status = ctk.CTkLabel(top, text="Status: idle")
        self.status.pack(side="right", padx=10)

        # Referenzanzeige
        self.ref_label = ctk.CTkLabel(
            self,
            text="Referenzspannung: –",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00ff99"
        )
        self.ref_label.pack(pady=5)

        main = ctk.CTkFrame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("f", "U", "I", "eta", "db", "bew")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=22)

        for col, txt in zip(columns,
            ["f [Hz]", "U_rest [V]", "I_ableit [A]", "η", "Dämpfung [dB]", "Bewertung"]):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=110)

        self.tree.pack(side="left", fill="y")

        plot_frame = ctk.CTkFrame(main)
        plot_frame.pack(side="right", fill="both", expand=True)

        self.plot_mode = ctk.StringVar(value="eta")

        ctk.CTkOptionMenu(
            plot_frame,
            values=["eta", "U_rest", "I_ableit", "Dämpfung_dB"],
            variable=self.plot_mode,
            command=lambda _: self.update_plot()
        ).pack(anchor="ne", padx=5, pady=5)

        self.fig, self.ax = plt.subplots(figsize=(6, 4))
        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # --------------------------------------------------------
    def start(self):
        self.f_start = float(self.e_fstart.get())
        self.f_stop  = float(self.e_fstop.get())
        self.f_step  = float(self.e_fstep.get())
        self.v_amp   = float(self.e_vamp.get())
        self.shunt   = float(self.e_shunt.get())
        self.N_PERIODS = int(self.e_periods.get())
        self.N_AVG     = int(self.e_avg.get())

        self.freqs.clear()
        self.u_peaks.clear()
        self.i_peaks.clear()
        self.etas.clear()
        self.atten_dbs.clear()
        for i in self.tree.get_children():
            self.tree.delete(i)

        self.running = True
        threading.Thread(target=self.measurement_thread, daemon=True).start()

    # --------------------------------------------------------
    def ask_connect_dut(self):
        self.wait_for_dut_event.clear()

        def confirm():
            self.wait_for_dut_event.set()
            win.destroy()

        win = ctk.CTkToplevel(self)
        win.title("Prüfling anschließen")
        win.geometry("450x200")
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="Referenzmessung abgeschlossen.\n\n"
                 "Bitte Prüfling anschließen\n"
                 "und anschließend bestätigen.",
            justify="center",
            font=ctk.CTkFont(size=14)
        ).pack(padx=20, pady=30)

        ctk.CTkButton(
            win,
            text="Prüfling angeschlossen – Messung starten",
            command=confirm
        ).pack()

    # --------------------------------------------------------
    def measurement_thread(self):
        try:
            self.status.configure(text="Initialisiere Gerät")
            self.rtb = RsInstrument(f"TCPIP::{RTB_IP}::INSTR", True, False)

            self.rtb.write_str("WGEN:FUNC SIN")
            self.rtb.write_str(f"WGEN:VOLT {self.v_amp}")
            self.rtb.write_str("WGEN:OUTP ON")

            self.rtb.write_str("CHAN1:STAT ON")
            self.rtb.write_str("CHAN2:STAT ON")
            self.rtb.write_str("TRIG:A:MODE AUTO")

            # Referenzmessung
            f_ref = self.f_start
            ACQ_TIME = self.N_PERIODS / f_ref
            self.rtb.write_str(f"TIM:ACQT {ACQ_TIME}")
            self.rtb.write_str(f"WGEN:FREQ {f_ref}")
            time.sleep(SETTLING_TIME)

            self.rtb.write_str("SING")
            self.rtb.query_opc()

            u_ref = np.array(
                self.rtb.query_bin_or_ascii_float_list("FORM ASC;:CHAN1:DATA?")
            )
            fs = len(u_ref) / ACQ_TIME
            _, self.u_ref = fft_peak(u_ref, fs)

            self.after(0, lambda:
                self.ref_label.configure(
                    text=f"Referenzspannung (Peak): {self.u_ref:.4f} V"
                )
            )

            self.after(0, self.ask_connect_dut)
            self.wait_for_dut_event.wait()

            # Sweep
            f_gen = self.f_start
            while f_gen <= self.f_stop and self.running:
                ACQ_TIME = self.N_PERIODS / f_gen
                self.rtb.write_str(f"TIM:ACQT {ACQ_TIME}")
                self.rtb.write_str(f"WGEN:FREQ {f_gen}")
                time.sleep(SETTLING_TIME)

                U_vals, I_vals = [], []

                for _ in range(self.N_AVG):
                    self.rtb.write_str("SING")
                    self.rtb.query_opc()

                    u = np.array(self.rtb.query_bin_or_ascii_float_list(
                        "FORM ASC;:CHAN1:DATA?"))
                    i = np.array(self.rtb.query_bin_or_ascii_float_list(
                        "FORM ASC;:CHAN2:DATA?"))

                    fs = len(u) / ACQ_TIME
                    _, Up = fft_peak(u, fs)
                    _, Ip = fft_peak(i, fs)

                    U_vals.append(Up)
                    I_vals.append(Ip / self.shunt)

                U_peak = np.mean(U_vals)
                I_peak = np.mean(I_vals)

                eta = 1 - U_peak / self.u_ref
                atten_db = 20 * np.log10(U_peak / self.u_ref)
                bew = bewertung_eta(eta)

                self.freqs.append(f_gen)
                self.u_peaks.append(U_peak)
                self.i_peaks.append(I_peak)
                self.etas.append(eta)
                self.atten_dbs.append(atten_db)

                self.after(0, lambda f=f_gen, u=U_peak, i=I_peak,
                           e=eta, d=atten_db, b=bew:
                    self.tree.insert("", "end",
                        values=(f, f"{u:.4f}", f"{i:.6e}",
                                f"{e:.3f}", f"{d:.1f}", b)))

                self.after(0, self.update_plot)
                f_gen += self.f_step

            self.status.configure(text="Messung abgeschlossen")

        finally:
            if self.rtb:
                self.rtb.write_str("WGEN:OUTP OFF")
                self.rtb.close()

    # --------------------------------------------------------
    def update_plot(self):
        self.ax.clear()
        mode = self.plot_mode.get()

        if mode == "eta":
            y = self.etas
            self.ax.set_ylabel("η")
        elif mode == "U_rest":
            y = self.u_peaks
            self.ax.set_ylabel("U_rest [V]")
        elif mode == "I_ableit":
            y = self.i_peaks
            self.ax.set_ylabel("I_ableit [A]")
        else:
            y = self.atten_dbs
            self.ax.set_ylabel("Dämpfung [dB]")

        self.ax.plot(self.freqs, y, marker="o")
        self.ax.set_xlabel("Frequenz [Hz]")
        self.ax.grid(True)
        self.canvas.draw_idle()


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AbleitsystemGUI()
    app.mainloop()