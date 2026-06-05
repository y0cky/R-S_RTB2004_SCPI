import os
import glob
import time
import csv
import threading
import queue
from datetime import datetime
from collections import deque

import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import numpy as np
import pyvisa

# Matplotlib Einbindung
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

# Design-Thema festlegen
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# ==========================================
# TAB 1: Live Sync Datalogger (FFT & Stats)
# ==========================================
class SyncLoggerFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        
        self.running = False
        self.data_queue = queue.Queue()
        self.device = None
        self.log_dir = ""

        self.labels = ["RMS", "MEAN", "STD", "PEAK+", "PEAK-", "POS_PULSE", "NEG_PULSE"]
        self.time_data = deque(maxlen=500)
        self.data_storage = {label: deque(maxlen=500) for label in self.labels}
        self.start_time = 0

        # Grid Layout für Hauptbereiche
        self.grid_columnconfigure(0, weight=0, minsize=250) # Sidebar
        self.grid_columnconfigure(1, weight=1)              # Plots
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_plots()

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=250, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        ctk.CTkLabel(sidebar, text="Sync Datalogger", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        
        # IP & Intervall
        ctk.CTkLabel(sidebar, text="IP-Adresse:", anchor="w").pack(fill="x", padx=15)
        self.ip_entry = ctk.CTkEntry(sidebar)
        self.ip_entry.insert(0, "192.168.0.100")
        self.ip_entry.pack(fill="x", padx=15, pady=2)
        
        ctk.CTkLabel(sidebar, text="Intervall (s):", anchor="w").pack(fill="x", padx=15)
        self.interval_entry = ctk.CTkEntry(sidebar)
        self.interval_entry.insert(0, "1.0")
        self.interval_entry.pack(fill="x", padx=15, pady=2)

        self.log_fft_var = tk.BooleanVar(value=True)
        self.chk_log_fft = ctk.CTkCheckBox(sidebar, text="FFT mitloggen & anzeigen", variable=self.log_fft_var)
        self.chk_log_fft.pack(padx=15, pady=10, anchor="w")

        # Buttons
        self.btn_start = ctk.CTkButton(sidebar, text="Start Logging", command=self.start, fg_color="green", hover_color="darkgreen")
        self.btn_start.pack(fill="x", padx=15, pady=5)
        self.btn_stop = ctk.CTkButton(sidebar, text="Stop Logging", command=self.stop, state="disabled", fg_color="red", hover_color="darkred")
        self.btn_stop.pack(fill="x", padx=15, pady=5)

        # Werteanzeige in Sidebar
        ctk.CTkLabel(sidebar, text="--- Live Werte ---", font=ctk.CTkFont(weight="bold")).pack(pady=(15,5))
        self.val_labels = {}
        for label in self.labels:
            lbl = ctk.CTkLabel(sidebar, text=f"{label}: ---", font=("Consolas", 11), anchor="w")
            lbl.pack(fill="x", padx=20, pady=1)
            self.val_labels[label] = lbl

    def _build_plots(self):
        plot_container = ctk.CTkFrame(self)
        plot_container.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        
        # Erzeuge 4 Subplots: 1 großes oben für FFT, 3 kleinere unten für Stats
        self.fig = Figure(figsize=(8, 9), dpi=90)
        self.fig.patch.set_facecolor('#2b2b2b')
        
        # FFT Plot (oben, erstreckt sich über die Breite)
        self.ax_fft = self.fig.add_subplot(4, 1, 1)
        # Trend Plots (unten)
        self.ax_stat = self.fig.add_subplot(4, 1, 2)
        self.ax_peak = self.fig.add_subplot(4, 1, 3)
        self.ax_pulse = self.fig.add_subplot(4, 1, 4)

        # Lines Definition
        self.line_fft_curr, = self.ax_fft.plot([], [], color='#1f77b4', label='FFT Aktuell', linewidth=1)
        self.line_fft_avg, = self.ax_fft.plot([], [], color='#ff7f0e', label='FFT Average', linewidth=1.5)
        
        self.lines = {
            "RMS": self.ax_stat.plot([], [], color='cyan', label='RMS')[0],
            "MEAN": self.ax_stat.plot([], [], color='magenta', label='MEAN')[0],
            "STD": self.ax_stat.plot([], [], color='lime', label='STD')[0],
            "PEAK+": self.ax_peak.plot([], [], color='orange', label='PEAK+')[0],
            "PEAK-": self.ax_peak.plot([], [], color='red', label='PEAK-')[0],
            "POS_PULSE": self.ax_pulse.plot([], [], color='blue', label='POS_PULSE')[0],
            "NEG_PULSE": self.ax_pulse.plot([], [], color='purple', label='NEG_PULSE')[0]
        }
        
        # Achsen-Styling
        axes_titles = [(self.ax_fft, "Live FFT Spektrum (dB)"), (self.ax_stat, "Statistik"), (self.ax_peak, "Spitzenwerte"), (self.ax_pulse, "Impulse")]
        for ax, title in axes_titles:
            ax.set_title(title, color='white', fontsize=10)
            ax.set_facecolor('#333333')
            ax.tick_params(colors='white', labelsize=8)
            ax.grid(True, color='#555555', linestyle=":")
            ax.legend(loc='upper left', fontsize='x-small')

        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, plot_container)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        
        self.after(100, self.update_gui)

    def start(self):
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.start_time = time.time()
        
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = f"SyncLog_{timestamp_str}"
        os.makedirs(self.log_dir, exist_ok=True)
        
        threading.Thread(target=self.measurement_thread, daemon=True).start()

    def stop(self):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def measurement_thread(self):
        csv_file = None
        try:
            rm = pyvisa.ResourceManager('@py')
            self.device = rm.open_resource(f"TCPIP::{self.ip_entry.get().strip()}::5025::SOCKET")
            self.device.read_termination, self.device.write_termination, self.device.timeout = '\n', '\n', 6000
            
            for i in range(1, 8): self.device.write(f"MEAS{i}:ENAB ON")
            
            start_freq, stop_freq = 0.0, 0.0
            if self.log_fft_var.get():
                self.device.write("FORM:DATA REAL,32")
                self.device.write("FORM:BORD LSBF")
                start_freq = float(self.device.query("SPECtrum:FREQuency:STARt?"))
                stop_freq = float(self.device.query("SPECtrum:FREQuency:STOP?"))

            csv_file = open(os.path.join(self.log_dir, "statistics.csv"), "w", newline="")
            csv_writer = csv.writer(csv_file, delimiter=";")
            csv_writer.writerow(["Timestamp", "Time"] + self.labels)

            fft_counter = 0

            while self.running:
                loop_start = time.time()
                current_time_str = datetime.now().strftime("%H:%M:%S")
                rel_time = time.time() - self.start_time
                
                # 1. Stats abfragen
                vals = []
                for i in range(1, 8):
                    try:
                        val = float(self.device.query(f"MEAS{i}:RES:ACT?"))
                        vals.append(0.0 if val > 1e20 else val)
                    except: vals.append(0.0)
                
                csv_writer.writerow([current_time_str, f"{rel_time:.2f}"] + [f"{v:.4f}" for v in vals])
                csv_file.flush()

                # 2. FFT abfragen
                fft_curr_data, fft_avg_data = None, None
                fft_filename = ""
                if self.log_fft_var.get():
                    try:
                        fft_curr_data = self.device.query_binary_values("SPECtrum:WAVeform:SPECtrum:DATA?", datatype='f', container=np.ndarray)
                        try: fft_avg_data = self.device.query_binary_values("SPECtrum:WAVeform:AVERage:DATA?", datatype='f', container=np.ndarray)
                        except: fft_avg_data = None
                        
                        frequencies = np.linspace(start_freq, stop_freq, len(fft_curr_data))
                        fft_filename = f"FFT_{fft_counter:05d}.csv"
                        
                        fft_filepath = os.path.join(self.log_dir, fft_filename)
                        if fft_avg_data is not None:
                            header = "Frequenz(Hz),Amplitude_Aktuell(dB),Amplitude_Average(dB)"
                            matrix = np.column_stack((frequencies, fft_curr_data, fft_avg_data))
                        else:
                            header = "Frequenz(Hz),Amplitude(dB)"
                            matrix = np.column_stack((frequencies, fft_curr_data))
                            
                        np.savetxt(fft_filepath, matrix, delimiter=",", header=header, comments="")
                        fft_counter += 1
                    except Exception as e:
                        print(f"FFT Error: {e}")

                # Daten an die GUI übergeben
                self.data_queue.put({
                    'time': current_time_str, 
                    'rel': rel_time, 
                    'vals': vals, 
                    'fft_curr': fft_curr_data, 
                    'fft_avg': fft_avg_data,
                    'fft_freq': frequencies if fft_curr_data is not None else None
                })
                
                try: interval = float(self.interval_entry.get())
                except: interval = 1.0
                sleep_time = interval - (time.time() - loop_start)
                if sleep_time > 0: time.sleep(sleep_time)

        except Exception as e:
            print(f"Logger Error: {e}")
            self.running = False
        finally:
            if self.device: 
                try: self.device.close()
                except: pass
            if csv_file: csv_file.close()

    def update_gui(self):
        if not self.winfo_exists(): return
        needs_redraw = False
        
        while not self.data_queue.empty():
            data = self.data_queue.get()
            self.time_data.append(data['rel'])
            
            # Trend-Graphen & Labels updaten
            for i, label in enumerate(self.labels):
                val = data['vals'][i]
                self.data_storage[label].append(val)
                self.lines[label].set_data(list(self.time_data), list(self.data_storage[label]))
                self.val_labels[label].configure(text=f"{label}: {val:.3f}")
            
            # Live FFT-Plot updaten
            if data['fft_curr'] is not None:
                freq_scale = data['fft_freq'] / 1e3 if data['fft_freq'][-1] >= 1e5 else data['fft_freq']
                self.line_fft_curr.set_data(freq_scale, data['fft_curr'])
                if data['fft_avg'] is not None:
                    self.line_fft_avg.set_data(freq_scale, data['fft_avg'])
                
                self.ax_fft.relim()
                self.ax_fft.autoscale_view()
                
            needs_redraw = True

        if needs_redraw:
            for ax in [self.ax_stat, self.ax_peak, self.ax_pulse]:
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw_idle()
            
        self.after(100, self.update_gui)


# ==========================================
# TAB 2: Offline Sync Viewer (FFT & Stats)
# ==========================================
class SyncViewerFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        
        self.log_dir = ""
        self.stat_data = [] # Beinhaltet Zeilen aus statistics.csv
        self.fft_files = []
        self.current_index = 0

        self.grid_columnconfigure(0, weight=0, minsize=250)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_plot()

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        ctk.CTkLabel(self.sidebar, text="Sync Offline Viewer", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        ctk.CTkButton(self.sidebar, text="Log-Ordner öffnen", command=self.load_log_folder, font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=15, pady=10)
        
        self.folder_label = ctk.CTkLabel(self.sidebar, text="Kein Ordner geladen", text_color="gray", font=ctk.CTkFont(size=11), justify="left")
        self.folder_label.pack(fill="x", padx=15)

        self.time_label = ctk.CTkLabel(self.sidebar, text="Zeit: --:--:-- (0.00s)", font=ctk.CTkFont(weight="bold"), anchor="w")
        self.time_label.pack(fill="x", padx=15, pady=10)

        # Slider & Nav
        ctk.CTkLabel(self.sidebar, text="Zeitleiste:").pack(fill="x", padx=15, anchor="w")
        self.time_slider = ctk.CTkSlider(self.sidebar, from_=0, to=1, command=self.on_slider_change, state="disabled")
        self.time_slider.pack(fill="x", padx=15, pady=5)
        
        nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=15, pady=5)
        self.btn_prev = ctk.CTkButton(nav_frame, text="<", width=50, command=self.step_prev, state="disabled")
        self.btn_prev.pack(side="left", fill="x", expand=True, padx=(0,2))
        self.btn_next = ctk.CTkButton(nav_frame, text=">", width=50, command=self.step_next, state="disabled")
        self.btn_next.pack(side="right", fill="x", expand=True, padx=(2,0))

        # Historische Werte-Anzeige
        ctk.CTkLabel(self.sidebar, text="--- Messwerte zu diesem Zeitpunkt ---", font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(20, 5))
        self.labels = ["RMS", "MEAN", "STD", "PEAK+", "PEAK-", "POS_PULSE", "NEG_PULSE"]
        self.val_labels = {}
        for label in self.labels:
            lbl = ctk.CTkLabel(self.sidebar, text=f"{label}: ---", font=("Consolas", 11), anchor="w")
            lbl.pack(fill="x", padx=20, pady=1)
            self.val_labels[label] = lbl

    def _build_plot(self):
        self.plot_frame = ctk.CTkFrame(self, corner_radius=10)
        self.plot_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        self.plot_frame.grid_columnconfigure(0, weight=1)
        self.plot_frame.grid_rowconfigure(0, weight=1)

        self.fig = Figure(figsize=(7, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Historisches FFT Spektrum")
        self.ax.set_xlabel("Frequenz")
        self.ax.set_ylabel("Amplitude (dB)")
        self.ax.grid(True, linestyle=":", alpha=0.6)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    def load_log_folder(self):
        folder_path = filedialog.askdirectory()
        if not folder_path: return
        
        stat_path = os.path.join(folder_path, "statistics.csv")
        if not os.path.exists(stat_path):
            messagebox.showerror("Fehler", "Ausgewählter Ordner enthält keine 'statistics.csv'!")
            return
            
        self.log_dir = folder_path
        
        # 1. Statistics einlesen
        self.stat_data = []
        with open(stat_path, "r") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader) # Header überspringen
            for row in reader:
                if row: self.stat_data.append(row)
                
        # 2. Zugehörige FFT-Dateien suchen
        self.fft_files = sorted(glob.glob(os.path.join(folder_path, "FFT_*.csv")))
        
        if not self.stat_data:
            messagebox.showinfo("Leer", "Keine Log-Daten in der Datei gefunden.")
            return

        # GUI Steuerung freischalten
        max_idx = len(self.stat_data) - 1
        self.folder_label.configure(text=f"Ordner: {os.path.basename(folder_path)}\n{max_idx+1} Datenpunkte")
        
        if max_idx > 0:
            self.time_slider.configure(state="normal", from_=0, to=max_idx, number_of_steps=max_idx)
            self.btn_prev.configure(state="normal")
            self.btn_next.configure(state="normal")
        else:
            self.time_slider.configure(state="disabled")
            
        self.time_slider.set(0)
        self.update_view(0)

    def on_slider_change(self, value):
        idx = int(round(value))
        if idx != self.current_index: 
            self.update_view(idx)

    def step_prev(self):
        if self.current_index > 0:
            self.time_slider.set(self.current_index - 1)
            self.update_view(self.current_index - 1)

    def step_next(self):
        if self.current_index < len(self.stat_data) - 1:
            self.time_slider.set(self.current_index + 1)
            self.update_view(self.current_index + 1)

    def update_view(self, index):
        if index < 0 or index >= len(self.stat_data): return
        self.current_index = index
        
        row = self.stat_data[index]
        timestamp, rel_time = row[0], row[1]
        vals = row[2:]
        
        # Zeitanzeige aktualisieren
        self.time_label.configure(text=f"Zeit: {timestamp} ({rel_time}s)")
        
        # Seitenleisten-Statistiken aktualisieren
        for i, label in enumerate(self.labels):
            if i < len(vals):
                self.val_labels[label].configure(text=f"{label}: {float(vals[i]):.3f}")

        # FFT Plot aktualisieren falls Datei vorhanden
        if index < len(self.fft_files):
            try:
                data = np.loadtxt(self.fft_files[index], delimiter=",", skiprows=1)
                freq, curr = data[:, 0], data[:, 1]
                avg = data[:, 2] if data.shape[1] > 2 else None
                
                self.ax.clear()
                self.ax.grid(True, linestyle=":", alpha=0.6)
                self.ax.set_title(f"Historie: {timestamp} ({rel_time}s)")
                self.ax.set_ylabel("Amplitude (dB)")
                
                freq_scale = freq / 1e3 if freq[-1] >= 1e5 else freq
                self.ax.set_xlabel("Frequenz (kHz)" if freq[-1] >= 1e5 else "Frequenz (Hz)")

                self.ax.plot(freq_scale, curr, label="Aktuell", color="#1f77b4", alpha=0.7)
                if avg is not None:
                    self.ax.plot(freq_scale, avg, label="Average", color="#ff7f0e", linewidth=1.5)
                
                self.ax.legend(loc="upper right")
                self.canvas.draw_idle()
            except Exception as e:
                print(f"Fehler beim Laden der FFT-Datei: {e}")


# ==========================================
# HAUPTPROGRAMM (App-Container)
# ==========================================
class UltimateSyncApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RTB2004 Sync Logger & Viewer Suite")
        self.geometry("1400x900")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Tab-View initialisieren
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        # Tabs hinzufügen
        self.tab_logger = self.tabview.add("Automatische Messung (Live Datalogger)")
        self.tab_viewer = self.tabview.add("Daten-Viewer (Historie)")

        # Module in die Tabs laden
        self.logger_module = SyncLoggerFrame(self.tab_logger)
        self.logger_module.pack(fill="both", expand=True)

        self.viewer_module = SyncViewerFrame(self.tab_viewer)
        self.viewer_module.pack(fill="both", expand=True)

    def on_closing(self):
        """Sorgt für ein sauberes Beenden aller Background-Tasks."""
        if hasattr(self, 'logger_module'):
            self.logger_module.stop()
        self.destroy()

if __name__ == "__main__":
    app = UltimateSyncApp()
    app.mainloop()