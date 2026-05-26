import customtkinter as ctk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from RsInstrument import RsInstrument
import threading
import queue
import csv
import time
from datetime import datetime
from collections import deque

# =============================
# Konfiguration
# =============================
DEFAULT_IP = "192.168.0.50"
POLL_TIME = 0.1
CSV_FILE = "live_statistics_logger.csv"
MAX_PLOT_POINTS = 500

class RTB2004LiveLogger(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RTB2004 - Multi-Graph Pro Logger")
        self.geometry("1100x950")
        
        # Cleanup beim Schließen
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.running = False
        self.data_queue = queue.Queue()
        self.rtb = None
        self.csv_file = None
        self.csv_writer = None

        self.labels = ["RMS", "MEAN", "STD", "PEAK+", "PEAK-", "POS_PULSE", "NEG_PULSE"]
        self.time_data = deque(maxlen=MAX_PLOT_POINTS)
        self.data_storage = {label: deque(maxlen=MAX_PLOT_POINTS) for label in self.labels}
        self.start_time = 0

        self._build_gui()

    def _build_gui(self):
        ctk.set_appearance_mode("dark")
        
        # Header (IP + Steuerung)
        control_frame = ctk.CTkFrame(self)
        control_frame.pack(fill="x", padx=10, pady=5)
        
        self.ip_entry = ctk.CTkEntry(control_frame, width=150)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.pack(side="left", padx=5)
        
        self.btn_start = ctk.CTkButton(control_frame, text="Start", command=self.start)
        self.btn_start.pack(side="left", padx=5)
        
        self.btn_stop = ctk.CTkButton(control_frame, text="Stop", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        # Dashboard (Live-Werte)
        self.val_frame = ctk.CTkFrame(self)
        self.val_frame.pack(fill="x", padx=10, pady=5)
        self.val_labels = {}
        for label in self.labels:
            lbl = ctk.CTkLabel(self.val_frame, text=f"{label}: ---", font=("Consolas", 12))
            lbl.pack(side="left", padx=15)
            self.val_labels[label] = lbl

        # Plots
        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.fig, (self.ax1, self.ax2, self.ax3) = plt.subplots(3, 1, figsize=(8, 10), constrained_layout=True)
        self.fig.patch.set_facecolor('#2b2b2b')
        
        self.lines = {
            "RMS": self.ax1.plot([], [], color='cyan', label='RMS')[0],
            "MEAN": self.ax1.plot([], [], color='magenta', label='MEAN')[0],
            "STD": self.ax1.plot([], [], color='lime', label='STD')[0],
            "PEAK+": self.ax2.plot([], [], color='orange', label='PEAK+')[0],
            "PEAK-": self.ax2.plot([], [], color='red', label='PEAK-')[0],
            "POS_PULSE": self.ax3.plot([], [], color='blue', label='POS_PULSE')[0],
            "NEG_PULSE": self.ax3.plot([], [], color='purple', label='NEG_PULSE')[0]
        }
        
        for ax, title in zip([self.ax1, self.ax2, self.ax3], ["Statistik", "Spitzenwerte", "Impulse"]):
            ax.set_title(title, color='white')
            ax.set_facecolor('#333333')
            ax.tick_params(colors='white')
            ax.legend(loc='upper left', fontsize='small')
            ax.grid(True, color='#555555')

        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.after(100, self.update_gui)

    def start(self):
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.start_time = time.time()
        
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp_str}.csv"
        
        self.csv_file = open(filename, "w", newline="")
        self.csv_writer = csv.writer(self.csv_file, delimiter=";")
        self.csv_writer.writerow(["Timestamp", "Time"] + self.labels)
        
        threading.Thread(target=self.measurement_thread, daemon=True).start()

    def stop(self):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if self.csv_file: self.csv_file.close()

    def on_closing(self):
        self.running = False
        self.destroy()

    def measurement_thread(self):
        try:
            ip = self.ip_entry.get().strip()
            self.rtb = RsInstrument(f"TCPIP::{ip}::INSTR", True, False)
            for i in range(1, 8): self.rtb.write_str(f"MEAS{i}:ENAB ON")
            
            while self.running:
                vals = []
                for i in range(1, 8):
                    try:
                        val = float(self.rtb.query_str(f"MEAS{i}:RES:ACT?"))
                        vals.append(0.0 if val > 1e20 else val)
                    except:
                        vals.append(0.0)
                
                self.data_queue.put({'time': datetime.now().strftime("%H:%M:%S"), 'rel': time.time() - self.start_time, 'vals': vals})
                time.sleep(POLL_TIME)
        except Exception as e:
            print(f"Messfehler: {e}")
        finally:
            if self.rtb: self.rtb.close()

    def update_gui(self):
        if not self.winfo_exists(): return
            
        while not self.data_queue.empty():
            data = self.data_queue.get()
            if self.csv_writer:
                self.csv_writer.writerow([data['time'], f"{data['rel']:.2f}"] + [f"{v:.4f}" for v in data['vals']])
                self.csv_file.flush()
            
            self.time_data.append(data['rel'])
            for i, label in enumerate(self.labels):
                val = data['vals'][i]
                self.data_storage[label].append(val)
                self.lines[label].set_data(list(self.time_data), list(self.data_storage[label]))
                self.val_labels[label].configure(text=f"{label}: {val:.3f}")

            for ax in [self.ax1, self.ax2, self.ax3]:
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw_idle()
            
        self.after(100, self.update_gui)

if __name__ == "__main__":
    app = RTB2004LiveLogger()
    app.mainloop()