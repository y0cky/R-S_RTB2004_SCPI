import pyvisa
import numpy as np

# ResourceManager initialisieren
rm = pyvisa.ResourceManager('@py')

# IP-Adresse des RTB2004 anpassen!
device = rm.open_resource("TCPIP::192.168.0.100::5025::SOCKET")
device.read_termination = '\n'
device.write_termination = '\n'
device.timeout = 5000

print("Verbunden mit:", device.query("*IDN?").strip())

# 1. Daten-Format auf dem Oszilloskop einstellen
# REAL,32 = 32-Bit Fließkommazahlen (Float)
device.write("FORM:DATA REAL,32")

# WICHTIG: Byte-Reihenfolge auf Little-Endian (LSBF) umstellen.
# Ohne diesen Befehl sendet R&S in Big-Endian, was zu den 10^-38 Werten führt!
device.write("FORM:BORD LSBF")

# 2. Frequenzgrenzen für die Achsenberechnung auslesen
start_freq = float(device.query("SPECtrum:FREQuency:STARt?"))
stop_freq = float(device.query("SPECtrum:FREQuency:STOP?"))

print(f"Frequenzbereich: {start_freq/1e3:.2f} kHz bis {stop_freq/1e3:.2f} kHz")
print(f"Lese FFT-Kurve binär aus...")

# 3. Binäre Daten abfragen
# Hinweis: Wenn Sie den echten Spektrumanalyse-Modus am Gerät nutzen, 
# ist "SPECtrum:WAVeform:SPECtrum:DATA?" der präziseste Befehl.
# Sollten Sie die klassische "Math"-Funktion nutzen, ändern Sie es zurück zu "CALCulate:MATH:DATA?"
fft_values = device.query_binary_values("SPECtrum:WAVeform:SPECtrum:DATA?", datatype='f', container=np.ndarray)

print(f"[ERFOLG] {len(fft_values)} Punkte empfangen.")
print(f"Erste 5 Amplitudenwerte (dB): {fft_values[:5]}")

# 4. Frequenzachse mathematisch exakt berechnen
frequencies = np.linspace(start_freq, stop_freq, len(fft_values))

# 5. Daten in CSV-Datei exportieren
csv_filename = "rtb2004_fft_echt_korrigiert.csv"
np.savetxt(csv_filename, np.column_stack((frequencies, fft_values)), 
           delimiter=",", header="Frequenz(Hz),Amplitude(dB)", comments="")

print(f"Datei erfolgreich als '{csv_filename}' gespeichert!")

# Verbindung sauber schließen
device.close()