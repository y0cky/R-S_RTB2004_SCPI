
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PCT Live Monitor für Oszilloskope mit SCPI/VISA (z. B. Rohde & Schwarz RTB24)
-----------------------------------------------------------------------------
Funktionen:
- Live-Erfassung der Wellenform von CH1 über LAN/VISA
- Live-Berechnung des PCT-Werts (Percent Contact Time)
- Zwei Erkennungsmodi:
    1) absoluter Schwellwert auf |Signal|
    2) Schwellwert auf |dV/dt|
- Optionaler Simulationsmodus zum Testen ohne Oszilloskop
- Live-Plots für Wellenform, Kontaktmaske und PCT-Verlauf
- CSV-Logging aller Kennwerte

Getestet als Standalone-Skript, ohne Projektstruktur.
Benötigte Pakete:
    pip install numpy matplotlib pyvisa pyvisa-py

Hinweis:
Die konkreten SCPI-Kommandos können je nach Firmware leicht variieren.
Wenn CHAN1:DATA? auf deinem RTB24 nicht funktioniert, bitte den in deinem Setup
funktionierenden Datenabrufbefehl einsetzen.
"""

import csv
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except Exception:
    pyvisa = None
    PYVISA_AVAILABLE = False


APP_TITLE = "PCT Live Monitor"
APP_VERSION = "1.0"
DEFAULT_RESOURCE = "TCPIP::192.168.0.100::5025::SOCKET"
DEFAULT_INTERVAL_S = 0.3
DEFAULT_THRESHOLD_V = 0.15
DEFAULT_DVDT_THRESHOLD = 150.0  # V/s
DEFAULT_MAX_POINTS = 600
DEFAULT_WAVE_DECIMATION = 1
DEFAULT_OUT_DIR = os.path.abspath("./pct_output")
DEFAULT_MIN_CONTACT_SAMPLES = 3


@dataclass
class AcquisitionConfig:
    resource: str
    channel: str
    interval_s: float
    threshold_mode: str      # "amplitude" oder "dvdt"
    threshold_value: float
    adaptive_enabled: bool
    adaptive_k: float
    min_contact_samples: int
    output_dir: str
    filename_prefix: str
    simulation: bool


class ScopeClient:
    """Einfacher VISA-Client für SCPI-Oszilloskope."""

    def __init__(self, resource: str):
        self.resource = resource
        self.rm = None
        self.dev = None

    def connect(self):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("pyvisa ist nicht installiert. Bitte 'pip install pyvisa pyvisa-py' ausführen.")
        self.rm = pyvisa.ResourceManager('@py')
        self.dev = self.rm.open_resource(self.resource)
        self.dev.read_termination = '\n'
        self.dev.write_termination = '\n'
        self.dev.timeout = 8000

    def close(self):
        try:
            if self.dev is not None:
                self.dev.close()
        except Exception:
            pass
        try:
            if self.rm is not None:
                self.rm.close()
        except Exception:
            pass
        self.dev = None
        self.rm = None

    def setup_waveform_transfer(self, channel: str = 'CHAN1'):
        if self.dev is None:
            raise RuntimeError("Gerät nicht verbunden")
        # Diese Befehle funktionieren in vielen R&S-Setups. Falls dein Gerät einen
        # abweichenden Datenpfad braucht, hier anpassen.
        self.dev.write('FORM:DATA REAL,32')
        self.dev.write('FORM:BORD LSBF')
        # Kanal aktivieren – falls bereits aktiv, harmlos.
        try:
            self.dev.write(f'{channel}:STAT ON')
        except Exception:
            pass

    def identify(self) -> str:
        return self.dev.query('*IDN?').strip()

    def get_x_increment(self, channel: str = 'CHAN1') -> float:
        try:
            return float(self.dev.query(f'{channel}:DATA:XINC?'))
        except Exception:
            return 1.0

    def acquire_waveform(self, channel: str = 'CHAN1') -> Tuple[np.ndarray, float]:
        if self.dev is None:
            raise RuntimeError("Gerät nicht verbunden")
        x_inc = self.get_x_increment(channel)
        data = self.dev.query_binary_values(f'{channel}:DATA?', datatype='f', container=np.ndarray)
        return data.astype(np.float32, copy=False), float(x_inc)


class PCTAnalyzer:
    """Berechnet PCT und Zusatzkennwerte aus der Wellenform."""

    @staticmethod
    def _remove_short_runs(mask: np.ndarray, min_len: int) -> np.ndarray:
        if min_len <= 1 or mask.size == 0:
            return mask
        cleaned = mask.copy()
        start = None
        for i, v in enumerate(mask):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if i - start < min_len:
                    cleaned[start:i] = False
                start = None
        if start is not None and (len(mask) - start) < min_len:
            cleaned[start:] = False
        return cleaned

    @staticmethod
    def compute_metrics(
        wave: np.ndarray,
        x_inc: float,
        threshold_mode: str,
        threshold_value: float,
        adaptive_enabled: bool = False,
        adaptive_k: float = 5.0,
        min_contact_samples: int = 3,
    ) -> dict:
        if wave is None or len(wave) == 0:
            return {
                'pct': 0.0,
                'contacts_count': 0,
                'contact_time_s': 0.0,
                'total_time_s': 0.0,
                'rms': 0.0,
                'mean': 0.0,
                'std': 0.0,
                'peak_pos': 0.0,
                'peak_neg': 0.0,
                'crest_factor': 0.0,
                'threshold_used': threshold_value,
                'mask': np.zeros(0, dtype=bool),
                'score_signal': np.zeros(0, dtype=np.float32),
            }

        wave = np.asarray(wave, dtype=np.float32)
        total_time_s = float(len(wave) * x_inc)
        abs_wave = np.abs(wave)

        rms = float(np.sqrt(np.mean(wave ** 2)))
        mean = float(np.mean(wave))
        std = float(np.std(wave))
        peak_pos = float(np.max(wave))
        peak_neg = float(np.min(wave))
        crest_factor = float(np.max(abs_wave) / rms) if rms > 1e-12 else 0.0

        if threshold_mode == 'amplitude':
            score_signal = abs_wave
            threshold = float(threshold_value)
            if adaptive_enabled:
                threshold = float(np.mean(score_signal) + adaptive_k * np.std(score_signal))
            mask = score_signal >= threshold
        else:
            dv = np.diff(wave, prepend=wave[0])
            dvdt = np.abs(dv / x_inc) if x_inc > 0 else np.abs(dv)
            score_signal = dvdt.astype(np.float32, copy=False)
            threshold = float(threshold_value)
            if adaptive_enabled:
                threshold = float(np.mean(score_signal) + adaptive_k * np.std(score_signal))
            mask = score_signal >= threshold

        mask = PCTAnalyzer._remove_short_runs(mask, max(1, int(min_contact_samples)))
        contact_time_s = float(np.sum(mask) * x_inc)
        pct = float(100.0 * contact_time_s / total_time_s) if total_time_s > 0 else 0.0

        transitions = np.diff(mask.astype(np.int8), prepend=0)
        contacts_count = int(np.sum(transitions == 1))

        return {
            'pct': pct,
            'contacts_count': contacts_count,
            'contact_time_s': contact_time_s,
            'total_time_s': total_time_s,
            'rms': rms,
            'mean': mean,
            'std': std,
            'peak_pos': peak_pos,
            'peak_neg': peak_neg,
            'crest_factor': crest_factor,
            'threshold_used': threshold,
            'mask': mask,
            'score_signal': score_signal,
        }


class PCTLiveApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry('1450x900')
        self.minsize(1250, 780)

        self.running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.data_queue: queue.Queue = queue.Queue()
        self.status_queue: queue.Queue = queue.Queue()
        self.scope: Optional[ScopeClient] = None
        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None
        self.measurement_started_at = None

        self.times = []
        self.pct_values = []
        self.rms_values = []
        self.crest_values = []
        self.contact_count_values = []

        self._build_vars()
        self._build_ui()
        self.after(100, self._poll_queues)

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------
    def _build_vars(self):
        self.var_resource = tk.StringVar(value=DEFAULT_RESOURCE)
        self.var_channel = tk.StringVar(value='CHAN1')
        self.var_interval = tk.StringVar(value=str(DEFAULT_INTERVAL_S))
        self.var_mode = tk.StringVar(value='amplitude')
        self.var_threshold = tk.StringVar(value=str(DEFAULT_THRESHOLD_V))
        self.var_adaptive = tk.BooleanVar(value=False)
        self.var_adaptive_k = tk.StringVar(value='5.0')
        self.var_min_contact_samples = tk.StringVar(value=str(DEFAULT_MIN_CONTACT_SAMPLES))
        self.var_output_dir = tk.StringVar(value=DEFAULT_OUT_DIR)
        self.var_filename_prefix = tk.StringVar(value='pct_live')
        self.var_simulation = tk.BooleanVar(value=not PYVISA_AVAILABLE)
        self.var_status = tk.StringVar(value='Bereit')

        self.live_labels = {
            'pct': tk.StringVar(value='PCT: --- %'),
            'contacts': tk.StringVar(value='Kontakte: ---'),
            'contact_time': tk.StringVar(value='Kontaktzeit: --- ms'),
            'threshold': tk.StringVar(value='Threshold: ---'),
            'rms': tk.StringVar(value='RMS: --- V'),
            'crest': tk.StringVar(value='Crest: ---'),
        }

    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=360)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky='nsew')
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        # Einstellungen links
        lf_conn = ttk.LabelFrame(left, text='Verbindung / Aufnahme', padding=10)
        lf_conn.pack(fill='x', pady=(0, 8))
        self._add_entry(lf_conn, 'VISA Resource', self.var_resource)
        self._add_entry(lf_conn, 'Kanal', self.var_channel)
        self._add_entry(lf_conn, 'Intervall [s]', self.var_interval)
        ttk.Checkbutton(lf_conn, text='Simulationsmodus', variable=self.var_simulation).pack(anchor='w', pady=3)

        lf_pct = ttk.LabelFrame(left, text='PCT-Erkennung', padding=10)
        lf_pct.pack(fill='x', pady=(0, 8))
        row_mode = ttk.Frame(lf_pct)
        row_mode.pack(fill='x', pady=2)
        ttk.Label(row_mode, text='Modus', width=18).pack(side='left')
        mode_box = ttk.Combobox(row_mode, textvariable=self.var_mode, width=18, state='readonly', values=['amplitude', 'dvdt'])
        mode_box.pack(side='left', fill='x', expand=True)
        self._add_entry(lf_pct, 'Threshold', self.var_threshold)
        ttk.Checkbutton(lf_pct, text='Adaptiver Threshold (mean + k·std)', variable=self.var_adaptive).pack(anchor='w', pady=(2, 2))
        self._add_entry(lf_pct, 'Adaptive k', self.var_adaptive_k)
        self._add_entry(lf_pct, 'Min. Kontaktsamples', self.var_min_contact_samples)

        lf_log = ttk.LabelFrame(left, text='Logging', padding=10)
        lf_log.pack(fill='x', pady=(0, 8))
        self._add_entry(lf_log, 'Output-Ordner', self.var_output_dir)
        ttk.Button(lf_log, text='Ordner wählen', command=self._choose_dir).pack(fill='x', pady=(4, 2))
        self._add_entry(lf_log, 'Dateipräfix', self.var_filename_prefix)

        lf_cmd = ttk.LabelFrame(left, text='Steuerung', padding=10)
        lf_cmd.pack(fill='x', pady=(0, 8))
        self.btn_test = ttk.Button(lf_cmd, text='Verbindung testen', command=self.on_test_connection)
        self.btn_test.pack(fill='x', pady=2)
        self.btn_start = ttk.Button(lf_cmd, text='Start', command=self.on_start)
        self.btn_start.pack(fill='x', pady=2)
        self.btn_stop = ttk.Button(lf_cmd, text='Stop', command=self.on_stop, state='disabled')
        self.btn_stop.pack(fill='x', pady=2)

        lf_live = ttk.LabelFrame(left, text='Live-Werte', padding=10)
        lf_live.pack(fill='x', pady=(0, 8))
        for key in ['pct', 'contacts', 'contact_time', 'threshold', 'rms', 'crest']:
            ttk.Label(lf_live, textvariable=self.live_labels[key], font=('Consolas', 12, 'bold')).pack(anchor='w', pady=2)

        lf_status = ttk.LabelFrame(left, text='Status', padding=10)
        lf_status.pack(fill='both', expand=True)
        ttk.Label(lf_status, textvariable=self.var_status, wraplength=320, justify='left').pack(fill='x')
        self.txt_log = tk.Text(lf_status, height=12, wrap='word')
        self.txt_log.pack(fill='both', expand=True, pady=(8, 0))
        self.txt_log.configure(state='disabled')

        # Plots rechts
        self.fig = Figure(figsize=(10, 8), dpi=100)
        self.ax_wave = self.fig.add_subplot(311)
        self.ax_contact = self.fig.add_subplot(312)
        self.ax_pct = self.fig.add_subplot(313)
        self.fig.tight_layout(pad=1.8)

        self.line_wave, = self.ax_wave.plot([], [], color='tab:red', lw=1.0, label='Wellenform')
        self.line_thr_pos, = self.ax_wave.plot([], [], color='tab:green', lw=1.0, ls='--', label='+Threshold')
        self.line_thr_neg, = self.ax_wave.plot([], [], color='tab:green', lw=1.0, ls='--', label='-Threshold')
        self.ax_wave.set_title('Wellenform')
        self.ax_wave.set_xlabel('Zeit [ms]')
        self.ax_wave.set_ylabel('Spannung [V]')
        self.ax_wave.grid(True, linestyle=':')
        self.ax_wave.legend(loc='upper right')

        self.line_contact, = self.ax_contact.plot([], [], color='tab:blue', lw=1.0, label='Kontaktmaske')
        self.line_score, = self.ax_contact.plot([], [], color='tab:orange', lw=0.8, alpha=0.8, label='Score')
        self.ax_contact.set_title('Kontaktmaske / Erkennungssignal')
        self.ax_contact.set_xlabel('Zeit [ms]')
        self.ax_contact.set_ylabel('Kontakt [0/1] / Score')
        self.ax_contact.grid(True, linestyle=':')
        self.ax_contact.legend(loc='upper right')

        self.line_pct, = self.ax_pct.plot([], [], color='tab:purple', lw=1.5, label='PCT [%]')
        self.line_rms, = self.ax_pct.plot([], [], color='tab:cyan', lw=1.0, alpha=0.8, label='RMS [V]')
        self.ax_pct.set_title('Live-Verlauf')
        self.ax_pct.set_xlabel('Zeit seit Start [s]')
        self.ax_pct.set_ylabel('Wert')
        self.ax_pct.grid(True, linestyle=':')
        self.ax_pct.legend(loc='upper right')

        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky='nsew')

    def _add_entry(self, parent, label, variable):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=2)
        ttk.Label(row, text=label, width=18).pack(side='left')
        ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)

    def _choose_dir(self):
        selected = filedialog.askdirectory(initialdir=self.var_output_dir.get() or os.getcwd())
        if selected:
            self.var_output_dir.set(selected)

    def _append_log(self, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self.txt_log.configure(state='normal')
        self.txt_log.insert('end', f'[{ts}] {msg}\n')
        self.txt_log.see('end')
        self.txt_log.configure(state='disabled')

    # ------------------------------------------------------------------
    # Config / CSV
    # ------------------------------------------------------------------
    def _collect_config(self) -> AcquisitionConfig:
        interval_s = float(self.var_interval.get().replace(',', '.'))
        threshold_value = float(self.var_threshold.get().replace(',', '.'))
        adaptive_k = float(self.var_adaptive_k.get().replace(',', '.'))
        min_contact_samples = int(float(self.var_min_contact_samples.get().replace(',', '.')))
        if interval_s <= 0:
            raise ValueError('Intervall muss > 0 sein.')
        if min_contact_samples <= 0:
            raise ValueError('Min. Kontaktsamples muss >= 1 sein.')

        cfg = AcquisitionConfig(
            resource=self.var_resource.get().strip(),
            channel=self.var_channel.get().strip().upper() or 'CHAN1',
            interval_s=interval_s,
            threshold_mode=self.var_mode.get().strip().lower(),
            threshold_value=threshold_value,
            adaptive_enabled=bool(self.var_adaptive.get()),
            adaptive_k=adaptive_k,
            min_contact_samples=min_contact_samples,
            output_dir=self.var_output_dir.get().strip(),
            filename_prefix=self.var_filename_prefix.get().strip() or 'pct_live',
            simulation=bool(self.var_simulation.get()),
        )
        return cfg

    def _open_csv(self, cfg: AcquisitionConfig):
        os.makedirs(cfg.output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.csv_path = os.path.join(cfg.output_dir, f'{cfg.filename_prefix}_{timestamp}.csv')
        self.csv_file = open(self.csv_path, 'w', newline='', encoding='utf-8')
        self.csv_writer = csv.writer(self.csv_file, delimiter=';')
        self.csv_writer.writerow([
            'timestamp_iso', 't_rel_s', 'pct_percent', 'contacts_count', 'contact_time_s', 'total_time_s',
            'threshold_mode', 'threshold_used', 'rms_v', 'mean_v', 'std_v', 'peak_pos_v', 'peak_neg_v', 'crest_factor'
        ])
        self.csv_file.flush()

    def _close_csv(self):
        try:
            if self.csv_file is not None:
                self.csv_file.close()
        except Exception:
            pass
        self.csv_file = None
        self.csv_writer = None

    # ------------------------------------------------------------------
    # Button-Callbacks
    # ------------------------------------------------------------------
    def on_test_connection(self):
        try:
            cfg = self._collect_config()
        except Exception as exc:
            messagebox.showerror('Ungültige Eingaben', str(exc))
            return

        def worker():
            try:
                if cfg.simulation:
                    self.status_queue.put(('log', 'Simulationsmodus aktiv – Verbindungstest übersprungen.'))
                    return
                test_scope = ScopeClient(cfg.resource)
                test_scope.connect()
                ident = test_scope.identify()
                test_scope.setup_waveform_transfer(cfg.channel)
                x_inc = test_scope.get_x_increment(cfg.channel)
                wave, _ = test_scope.acquire_waveform(cfg.channel)
                test_scope.close()
                self.status_queue.put(('log', f'Verbunden: {ident}'))
                self.status_queue.put(('log', f'Testaufnahme erfolgreich: {len(wave)} Samples, XINC={x_inc:.3e} s'))
            except Exception as exc:
                self.status_queue.put(('error', f'Verbindungstest fehlgeschlagen: {exc}'))

        threading.Thread(target=worker, daemon=True).start()

    def on_start(self):
        if self.running:
            return
        try:
            cfg = self._collect_config()
            self._open_csv(cfg)
        except Exception as exc:
            messagebox.showerror('Ungültige Eingaben', str(exc))
            return

        self.running = True
        self.measurement_started_at = time.time()
        self.times.clear()
        self.pct_values.clear()
        self.rms_values.clear()
        self.crest_values.clear()
        self.contact_count_values.clear()
        self._reset_plots()

        self.btn_start.configure(state='disabled')
        self.btn_stop.configure(state='normal')
        self.var_status.set('Messung läuft …')
        self._append_log('Messung gestartet.')
        self._append_log(f'CSV-Logging: {self.csv_path}')

        self.worker_thread = threading.Thread(target=self._measurement_loop, args=(cfg,), daemon=True)
        self.worker_thread.start()

    def on_stop(self):
        self.running = False
        self.btn_stop.configure(state='disabled')
        self.var_status.set('Stop angefordert …')
        self._append_log('Stop angefordert …')

    def _reset_plots(self):
        for line in [self.line_wave, self.line_thr_pos, self.line_thr_neg, self.line_contact, self.line_score, self.line_pct, self.line_rms]:
            line.set_data([], [])
        for ax in [self.ax_wave, self.ax_contact, self.ax_pct]:
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Messschleife
    # ------------------------------------------------------------------
    def _measurement_loop(self, cfg: AcquisitionConfig):
        scope = None
        try:
            if not cfg.simulation:
                scope = ScopeClient(cfg.resource)
                scope.connect()
                ident = scope.identify()
                scope.setup_waveform_transfer(cfg.channel)
                self.status_queue.put(('log', f'Oszilloskop verbunden: {ident}'))
            else:
                self.status_queue.put(('log', 'Simulationsmodus aktiv.'))

            sim_phase = 0.0
            while self.running:
                t0 = time.time()
                if cfg.simulation:
                    wave, x_inc, sim_phase = self._simulate_waveform(sim_phase)
                else:
                    wave, x_inc = scope.acquire_waveform(cfg.channel)

                metrics = PCTAnalyzer.compute_metrics(
                    wave=wave,
                    x_inc=x_inc,
                    threshold_mode=cfg.threshold_mode,
                    threshold_value=cfg.threshold_value,
                    adaptive_enabled=cfg.adaptive_enabled,
                    adaptive_k=cfg.adaptive_k,
                    min_contact_samples=cfg.min_contact_samples,
                )

                t_rel = time.time() - self.measurement_started_at
                packet = {
                    't_rel': t_rel,
                    'wave': wave,
                    'x_inc': x_inc,
                    'metrics': metrics,
                }
                self.data_queue.put(packet)

                if self.csv_writer is not None:
                    self.csv_writer.writerow([
                        datetime.now().isoformat(timespec='milliseconds'),
                        f'{t_rel:.6f}',
                        f"{metrics['pct']:.6f}",
                        metrics['contacts_count'],
                        f"{metrics['contact_time_s']:.9f}",
                        f"{metrics['total_time_s']:.9f}",
                        cfg.threshold_mode,
                        f"{metrics['threshold_used']:.9f}",
                        f"{metrics['rms']:.9f}",
                        f"{metrics['mean']:.9f}",
                        f"{metrics['std']:.9f}",
                        f"{metrics['peak_pos']:.9f}",
                        f"{metrics['peak_neg']:.9f}",
                        f"{metrics['crest_factor']:.9f}",
                    ])
                    self.csv_file.flush()

                remaining = cfg.interval_s - (time.time() - t0)
                if remaining > 0:
                    time.sleep(remaining)

        except Exception as exc:
            self.status_queue.put(('error', f'Messschleife abgebrochen: {exc}'))
        finally:
            self.running = False
            try:
                if scope is not None:
                    scope.close()
            except Exception:
                pass
            self._close_csv()
            self.status_queue.put(('stopped', 'Messung beendet.'))

    def _simulate_waveform(self, sim_phase: float) -> Tuple[np.ndarray, float, float]:
        # 10 ms Fenster, 100 kSa/s
        fs = 100_000.0
        duration = 0.010
        n = int(fs * duration)
        x_inc = 1.0 / fs
        t = np.arange(n, dtype=np.float32) * x_inc

        base = 0.03 * np.sin(2 * np.pi * 1200.0 * t + sim_phase)
        noise = 0.01 * np.random.randn(n).astype(np.float32)

        # Simulierte kurze Kontaktimpulse
        wave = base + noise
        pulse_count = np.random.randint(2, 12)
        for _ in range(pulse_count):
            idx = np.random.randint(0, max(1, n - 8))
            width = np.random.randint(2, 7)
            amp = np.random.choice([1.0, -1.0]) * np.random.uniform(0.18, 0.5)
            wave[idx:idx+width] += amp

        sim_phase = (sim_phase + 0.25) % (2 * np.pi)
        return wave.astype(np.float32, copy=False), x_inc, sim_phase

    # ------------------------------------------------------------------
    # Queue / Plot-Update
    # ------------------------------------------------------------------
    def _poll_queues(self):
        while not self.status_queue.empty():
            kind, msg = self.status_queue.get()
            if kind == 'log':
                self.var_status.set(msg)
                self._append_log(msg)
            elif kind == 'error':
                self.var_status.set(msg)
                self._append_log(msg)
                self.btn_start.configure(state='normal')
                self.btn_stop.configure(state='disabled')
                messagebox.showerror('Fehler', msg)
            elif kind == 'stopped':
                self.var_status.set(msg)
                self._append_log(msg)
                self.btn_start.configure(state='normal')
                self.btn_stop.configure(state='disabled')
                if self.csv_path:
                    self._append_log(f'CSV gespeichert: {self.csv_path}')

        redraw = False
        latest_packet = None
        while not self.data_queue.empty():
            latest_packet = self.data_queue.get()

        if latest_packet is not None:
            self._update_from_packet(latest_packet)
            redraw = True

        if redraw:
            self.canvas.draw_idle()

        self.after(100, self._poll_queues)

    def _update_from_packet(self, packet: dict):
        t_rel = packet['t_rel']
        wave = packet['wave']
        x_inc = packet['x_inc']
        metrics = packet['metrics']

        self.times.append(t_rel)
        self.pct_values.append(metrics['pct'])
        self.rms_values.append(metrics['rms'])
        self.crest_values.append(metrics['crest_factor'])
        self.contact_count_values.append(metrics['contacts_count'])

        if len(self.times) > DEFAULT_MAX_POINTS:
            self.times = self.times[-DEFAULT_MAX_POINTS:]
            self.pct_values = self.pct_values[-DEFAULT_MAX_POINTS:]
            self.rms_values = self.rms_values[-DEFAULT_MAX_POINTS:]
            self.crest_values = self.crest_values[-DEFAULT_MAX_POINTS:]
            self.contact_count_values = self.contact_count_values[-DEFAULT_MAX_POINTS:]

        # Live-Labels
        self.live_labels['pct'].set(f"PCT: {metrics['pct']:.3f} %")
        self.live_labels['contacts'].set(f"Kontakte: {metrics['contacts_count']}")
        self.live_labels['contact_time'].set(f"Kontaktzeit: {metrics['contact_time_s'] * 1000.0:.3f} ms")
        threshold_unit = 'V' if self.var_mode.get() == 'amplitude' else 'V/s'
        self.live_labels['threshold'].set(f"Threshold: {metrics['threshold_used']:.4f} {threshold_unit}")
        self.live_labels['rms'].set(f"RMS: {metrics['rms']:.4f} V")
        self.live_labels['crest'].set(f"Crest: {metrics['crest_factor']:.3f}")

        # Plot 1: Wellenform + Schwellwerte
        t_ms = np.arange(len(wave)) * x_inc * 1000.0
        dec = DEFAULT_WAVE_DECIMATION if DEFAULT_WAVE_DECIMATION > 1 else max(1, len(wave) // 4000)
        self.line_wave.set_data(t_ms[::dec], wave[::dec])
        thr = metrics['threshold_used']
        if self.var_mode.get() == 'amplitude':
            self.line_thr_pos.set_data(t_ms[::dec], np.full_like(t_ms[::dec], thr))
            self.line_thr_neg.set_data(t_ms[::dec], np.full_like(t_ms[::dec], -thr))
        else:
            # Im dV/dt-Modus bleibt die Wellenform sichtbar, die Grenzen sind hier weniger intuitiv.
            self.line_thr_pos.set_data([], [])
            self.line_thr_neg.set_data([], [])
        self.ax_wave.set_xlim(float(t_ms[0]), float(t_ms[-1]) if len(t_ms) else 1.0)
        self.ax_wave.relim()
        self.ax_wave.autoscale_view(scalex=False, scaley=True)

        # Plot 2: Kontaktmaske + Score
        mask = metrics['mask'].astype(np.float32)
        score = metrics['score_signal']
        if score.size:
            # Normierung für gemeinsame Darstellung mit der Maske
            denom = max(np.max(score), 1e-12)
            score_plot = 0.95 * score / denom
        else:
            score_plot = score
        self.line_contact.set_data(t_ms[::dec], mask[::dec])
        self.line_score.set_data(t_ms[::dec], score_plot[::dec] if len(score_plot) else score_plot)
        self.ax_contact.set_xlim(float(t_ms[0]), float(t_ms[-1]) if len(t_ms) else 1.0)
        self.ax_contact.set_ylim(-0.05, 1.05)
        self.ax_contact.relim()
        self.ax_contact.autoscale_view(scalex=False, scaley=False)

        # Plot 3: PCT + RMS Verlauf
        self.line_pct.set_data(self.times, self.pct_values)
        self.line_rms.set_data(self.times, self.rms_values)
        if self.times:
            self.ax_pct.set_xlim(max(0.0, self.times[0]), self.times[-1] + 1e-9)
        self.ax_pct.relim()
        self.ax_pct.autoscale_view(scalex=False, scaley=True)


def main():
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except Exception:
        pass
    app = PCTLiveApp()
    app.mainloop()


if __name__ == '__main__':
    main()
