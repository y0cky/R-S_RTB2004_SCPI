import customtkinter as ctk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# =============================
# GUI Klasse
# =============================
class WellenspannungGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Ideales Wellenspannungsmodell (PWM-basiert)")
        self.geometry("1200x700")

        self._build_gui()

    # -------------------------
    # GUI Aufbau
    # -------------------------
    def _build_gui(self):

        # --- Parameter Frame ---
        param = ctk.CTkFrame(self)
        param.pack(fill="x", padx=10, pady=5)

        self.u_dc = self._entry(param, "U_DC [V]", "600")
        self.f_el = self._entry(param, "f_el [Hz]", "50")
        self.f_pwm = self._entry(param, "f_PWM [Hz]", "8000")
        self.k_cm = self._entry(param, "k_cm [-]", "0.05")
        self.t_sim = self._entry(param, "T_sim [ms]", "5")

        ctk.CTkButton(
            param, text="Berechnen & Plotten",
            command=self.calculate
        ).pack(side="left", padx=10)

        # --- Plot Bereich ---
        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.fig, (self.ax_t, self.ax_f) = plt.subplots(2, 1, figsize=(10, 6))

        self.ax_t.set_title("Wellenspannung – Zeitdomäne")
        self.ax_t.set_xlabel("Zeit [ms]")
        self.ax_t.set_ylabel("Spannung [V]")
        self.ax_t.grid(True)

        self.ax_f.set_title("Wellenspannung – Frequenzdomäne (FFT)")
        self.ax_f.set_xlabel("Frequenz [Hz]")
        self.ax_f.set_ylabel("Amplitude [dB]")
        self.ax_f.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # -------------------------
    # Hilfsfunktionen
    # -------------------------
    def _entry(self, parent, label, default):
        frame = ctk.CTkFrame(parent)
        frame.pack(side="left", padx=5)
        ctk.CTkLabel(frame, text=label).pack()
        e = ctk.CTkEntry(frame, width=100)
        e.insert(0, default)
        e.pack()
        return e

    # -------------------------
    # Kerndefinition: Wellenspannung
    # -------------------------
    @staticmethod
    def wellenspannung(t, U_DC, f_el, f_pwm, k_cm):
        m = np.sin(2*np.pi*f_el*t)
        carrier = np.sign(np.sin(2*np.pi*f_pwm*t))
        s = np.sign(m - carrier)
        return k_cm * (U_DC / 6.0) * s

    # -------------------------
    # Berechnung & Plot
    # -------------------------
    def calculate(self):
        try:
            U_DC = float(self.u_dc.get())
            f_el = float(self.f_el.get())
            f_pwm = float(self.f_pwm.get())
            k_cm = float(self.k_cm.get())
            T_sim = float(self.t_sim.get()) / 1000.0

            fs = max(10*f_pwm, 500_000)
            t = np.arange(0, T_sim, 1/fs)

            # Wellenspannung berechnen
            u = self.wellenspannung(t, U_DC, f_el, f_pwm, k_cm)

            # FFT
            u_dc = u - np.mean(u)
            window = np.hanning(len(u))
            fft = np.fft.rfft(u_dc * window)

            freq = np.fft.rfftfreq(len(u), 1/fs)
            mag = 20*np.log10(np.abs(fft) + 1e-12)

            # Plots
            self.ax_t.clear()
            self.ax_f.clear()

            self.ax_t.plot(t*1e3, u)
            self.ax_t.set_title("Wellenspannung – Zeitdomäne")
            self.ax_t.set_xlabel("Zeit [ms]")
            self.ax_t.set_ylabel("Spannung [V]")
            self.ax_t.grid(True)

            self.ax_f.semilogx(freq, mag)
            self.ax_f.set_title("Wellenspannung – Frequenzdomäne (FFT)")
            self.ax_f.set_xlabel("Frequenz [Hz]")
            self.ax_f.set_ylabel("Amplitude [dB]")
            self.ax_f.grid(True)

            self.canvas.draw_idle()

        except Exception as e:
            print("Fehler:", e)


# =============================
# MAIN
# =============================
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = WellenspannungGUI()
    app.mainloop()