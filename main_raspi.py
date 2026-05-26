#10.py kalau di raspi quality gate


from pathlib import Path
import csv
import os
import time
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

# =========================
# OPTIONAL AUDIO: PYGAME
# =========================
try:
    import pygame
except ModuleNotFoundError:
    pygame = None
    print("PERINGATAN: pygame tidak ditemukan. Suara alarm tidak aktif.", flush=True)

# =========================
# OPTIONAL GPIO: RASPBERRY PI
# =========================
try:
    import RPi.GPIO as GPIO
except ModuleNotFoundError:
    GPIO = None
    print(
        "PERINGATAN: Modul RPi.GPIO tidak ditemukan. GPIO hanya aktif di Raspberry Pi.",
        flush=True,
    )


# =========================================================
# DATA QR / BARCODE UTAMA
#
# 13621 / 13622 dipakai untuk membaca jenis Balance Shaft pada scan 1.
# bs_1 / bs_2 dipakai sebagai kode acuan judgment pada scan 2.
# =========================================================
BS1_PRODUCT_CODE = "13621"   # Balance Shaft 1 pada barcode produk
BS2_PRODUCT_CODE = "13622"   # Balance Shaft 2 pada barcode produk

BS1_JUDGMENT_CODE = "bs_1"   # Kode acuan judgment Balance Shaft 1
BS2_JUDGMENT_CODE = "bs_2"   # Kode acuan judgment Balance Shaft 2


def get_product_balance_code(code):
    """
    Ambil kode produk dari barcode.

    Dipakai terutama untuk scan 1:
    - 13621 = Balance Shaft 1
    - 13622 = Balance Shaft 2
    """
    text = (code or "").strip()

    if BS1_PRODUCT_CODE in text:
        return BS1_PRODUCT_CODE

    if BS2_PRODUCT_CODE in text:
        return BS2_PRODUCT_CODE

    return ""


def get_judgment_balance_code(code):
    """
    Ambil kode acuan judgment dari barcode.

    Dipakai terutama untuk scan 2:
    - bs_1 = judgment Balance Shaft 1
    - bs_2 = judgment Balance Shaft 2

    Pengecekan dibuat case-insensitive supaya input BS_1, bs_1, atau Bs_1
    tetap terbaca sebagai kode yang sama.
    """
    text = (code or "").strip().lower()

    if BS1_JUDGMENT_CODE in text:
        return BS1_JUDGMENT_CODE

    if BS2_JUDGMENT_CODE in text:
        return BS2_JUDGMENT_CODE

    return ""


def get_display_code(code):
    """
    Untuk tampilan tabel/log.
    Jika barcode mengandung bs_1/bs_2, tampilkan kode judgment.
    Jika tidak, tampilkan kode produk 13621/13622.
    """
    judgment_code = get_judgment_balance_code(code)
    if judgment_code:
        return judgment_code

    product_code = get_product_balance_code(code)
    if product_code:
        return product_code

    return ""


# Rule judgment utama:
# - Jika scan 1 mengandung 13621, maka scan 2 OK harus mengandung bs_1.
# - Jika scan 1 mengandung 13622, maka scan 2 OK harus mengandung bs_2.
SCAN1_PRODUCT_CODE_TO_EXPECTED_SCAN2_JUDGMENT_CODE = {
    BS1_PRODUCT_CODE: BS1_JUDGMENT_CODE,
    BS2_PRODUCT_CODE: BS2_JUDGMENT_CODE,
}

VALID_SCAN1_CODES = set(SCAN1_PRODUCT_CODE_TO_EXPECTED_SCAN2_JUDGMENT_CODE.keys())

RESET_CODES = {
    "OI-234066",
    "OI-233948",
    "OI-010145",
    "OI-060760",
    "OI-99070",
    "OI-00103",
    "OI-213801",
    "OI-234036",
}


# =========================================================
# SETTING SCAN
# =========================================================
SCAN_2_TIMEOUT_SECONDS = 20
IGNORE_DUPLICATE_SCAN1_SECONDS = 0.8

# Dipakai ketika scanner tidak mengirim ENTER.
# Barcode dianggap selesai jika tidak ada karakter baru selama durasi ini.
BARCODE_IDLE_FINISH_SECONDS = 0.15

# Jika terlalu sensitif saat operator mengetik manual, naikkan ke 0.20 atau 0.25.
BARCODE_IDLE_FINISH_MS = int(BARCODE_IDLE_FINISH_SECONDS * 1000)


# =========================================================
# GPIO RELAY RASPBERRY PI 3 B+
# Mode BCM: GPIO 21 dan GPIO 20
# =========================================================
GPIO_149 = 20
GPIO_88 = 21

EXPECTED_SCAN2_CODE_TO_GPIO = {
    BS1_JUDGMENT_CODE: GPIO_149,  # bs_1 -> GPIO 21
    BS2_JUDGMENT_CODE: GPIO_88,   # bs_2 -> GPIO 20
}

# Active HIGH relay:
# GPIO LOW  = relay/lampu OFF
# GPIO HIGH = relay/lampu ON
RELAY_ON = None
RELAY_OFF = None

BLINK_INTERVAL_SECONDS = 0.5


# =========================================================
# FILE SUARA DAN DATA
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "Media"

SOUND_ALARM_PATH = MEDIA_DIR / "0003.mp3"
SOUND_SCAN_2_TIMEOUT_PATH = MEDIA_DIR / "0017.mp3"

FILE_SCAN = BASE_DIR / "data_scan_judgment.csv"
FILE_REKAP = BASE_DIR / "rekap_barcode.csv"


# =========================================================
# AUDIO STATE
# =========================================================
AUDIO_READY = False
ALARM_CHANNEL = None
VOICE_CHANNEL = None
SOUND_ALARM = None
SOUND_SCAN_2_TIMEOUT = None


def log_console(*args):
    print(*args, flush=True)


def load_sound(file_path):
    if not AUDIO_READY:
        return None

    if not file_path.exists():
        log_console(f"File suara tidak ditemukan: {file_path}")
        return None

    try:
        return pygame.mixer.Sound(str(file_path))
    except Exception as e:
        log_console(f"Gagal load suara: {file_path}")
        log_console("Error:", e)
        return None


def setup_audio():
    global AUDIO_READY, ALARM_CHANNEL, VOICE_CHANNEL, SOUND_ALARM, SOUND_SCAN_2_TIMEOUT

    if pygame is None:
        AUDIO_READY = False
        return

    try:
        pygame.mixer.init()
        pygame.mixer.set_num_channels(4)
        ALARM_CHANNEL = pygame.mixer.Channel(0)
        VOICE_CHANNEL = pygame.mixer.Channel(1)
        AUDIO_READY = True
    except Exception as e:
        AUDIO_READY = False
        log_console("PERINGATAN: Audio pygame gagal diinisialisasi.")
        log_console("Error:", e)
        return

    SOUND_ALARM = load_sound(SOUND_ALARM_PATH)
    SOUND_SCAN_2_TIMEOUT = load_sound(SOUND_SCAN_2_TIMEOUT_PATH)


def wait_sound_finish(channel, stop_event):
    if not AUDIO_READY or channel is None:
        return

    while channel.get_busy():
        if stop_event.is_set():
            channel.stop()
            break
        time.sleep(0.03)


def stop_all_sound():
    if AUDIO_READY:
        pygame.mixer.stop()


def play_alarm_loop():
    if not AUDIO_READY or SOUND_ALARM is None or ALARM_CHANNEL is None:
        return

    ALARM_CHANNEL.stop()
    ALARM_CHANNEL.play(SOUND_ALARM, loops=-1)


timeout_alarm_stop_event = threading.Event()
timeout_alarm_thread = None


def timeout_alarm_worker():
    if (
        not AUDIO_READY
        or SOUND_SCAN_2_TIMEOUT is None
        or SOUND_ALARM is None
        or ALARM_CHANNEL is None
        or VOICE_CHANNEL is None
    ):
        return

    while not timeout_alarm_stop_event.is_set():
        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()
        VOICE_CHANNEL.play(SOUND_SCAN_2_TIMEOUT)
        wait_sound_finish(VOICE_CHANNEL, timeout_alarm_stop_event)

        if timeout_alarm_stop_event.wait(0.15):
            break

        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()
        ALARM_CHANNEL.play(SOUND_ALARM)
        wait_sound_finish(ALARM_CHANNEL, timeout_alarm_stop_event)

        if timeout_alarm_stop_event.wait(0.15):
            break


def start_timeout_alarm_sequence():
    global timeout_alarm_thread

    stop_timeout_alarm_sequence()

    timeout_alarm_stop_event.clear()
    timeout_alarm_thread = threading.Thread(
        target=timeout_alarm_worker,
        daemon=True,
    )
    timeout_alarm_thread.start()


def stop_timeout_alarm_sequence():
    global timeout_alarm_thread

    timeout_alarm_stop_event.set()

    if AUDIO_READY and ALARM_CHANNEL is not None and VOICE_CHANNEL is not None:
        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()

    if timeout_alarm_thread is not None and timeout_alarm_thread.is_alive():
        timeout_alarm_thread.join(timeout=0.2)

    timeout_alarm_thread = None


def is_timeout_alarm_active():
    return timeout_alarm_thread is not None and timeout_alarm_thread.is_alive()


def is_any_alarm_active():
    if is_timeout_alarm_active():
        return True

    if AUDIO_READY and ALARM_CHANNEL is not None and ALARM_CHANNEL.get_busy():
        return True

    return False


# =========================================================
# GPIO
# =========================================================
relay_blink_stop_event = threading.Event()
relay_blink_thread = None
active_relay_pin = None


def setup_gpio():
    global RELAY_ON, RELAY_OFF

    if GPIO is None:
        return

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    RELAY_ON = GPIO.HIGH
    RELAY_OFF = GPIO.LOW

    GPIO.setup(GPIO_149, GPIO.OUT, initial=RELAY_OFF)
    GPIO.setup(GPIO_88, GPIO.OUT, initial=RELAY_OFF)

    turn_off_all_relays()


def gpio_output(pin, state):
    if GPIO is None:
        return

    GPIO.output(pin, state)


def turn_off_all_relays():
    if GPIO is None:
        return

    gpio_output(GPIO_149, RELAY_OFF)
    gpio_output(GPIO_88, RELAY_OFF)


def relay_blink_worker(pin):
    while not relay_blink_stop_event.is_set():
        gpio_output(pin, RELAY_ON)

        if relay_blink_stop_event.wait(BLINK_INTERVAL_SECONDS):
            break

        gpio_output(pin, RELAY_OFF)

        if relay_blink_stop_event.wait(BLINK_INTERVAL_SECONDS):
            break

    gpio_output(pin, RELAY_OFF)


def start_relay_blink(pin):
    global relay_blink_thread, active_relay_pin

    stop_relay_blink()

    active_relay_pin = pin
    relay_blink_stop_event.clear()

    relay_blink_thread = threading.Thread(
        target=relay_blink_worker,
        args=(pin,),
        daemon=True,
    )
    relay_blink_thread.start()


def stop_relay_blink():
    global relay_blink_thread, active_relay_pin

    relay_blink_stop_event.set()

    if relay_blink_thread is not None and relay_blink_thread.is_alive():
        relay_blink_thread.join(timeout=0.2)

    relay_blink_thread = None
    active_relay_pin = None
    turn_off_all_relays()


def cleanup():
    stop_timeout_alarm_sequence()
    stop_all_sound()
    stop_relay_blink()
    turn_off_all_relays()

    if GPIO is not None:
        GPIO.cleanup()

    if AUDIO_READY and pygame is not None:
        pygame.mixer.quit()


# =========================================================
# APLIKASI GUI
# =========================================================
class BarcodeJudgmentApp:
    STATE_SCAN_1 = "SCAN_1"
    STATE_SCAN_2 = "SCAN_2"
    STATE_LOCKED = "LOCKED"

    def __init__(self, root):
        self.root = root
        self.root.title("Program Scan Barcode Judgment")
        self.root.geometry("980x620")
        self.root.minsize(900, 560)

        self.state = self.STATE_SCAN_1
        self.system_locked = False

        self.scan_1_raw = ""
        self.scan_1_code = ""
        self.expected_scan_2_code = ""
        self.expected_relay_pin = None
        self.scan_1_time_monotonic = 0.0

        self.timeout_after_id = None
        self.idle_after_id = None
        self.focus_after_id = None

        self.data_rekap = {}

        self.setup_ui()
        self.load_existing_data()
        self.refocus_entry()
        self.log_startup()

    # -----------------------------------------------------
    # UI
    # -----------------------------------------------------
    def setup_ui(self):
        self.root.configure(bg="#f4f6f8")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        title = tk.Label(
            self.root,
            text="Program Scan QR + GPIO Relay + Judgment",
            font=("Arial", 18, "bold"),
            bg="#f4f6f8",
            fg="#17202a",
        )
        title.pack(pady=(12, 4))

        subtitle = tk.Label(
            self.root,
            text="Rule judgment: scan 1 mengandung 13621 maka scan 2 harus mengandung bs_1; scan 1 mengandung 13622 maka scan 2 harus mengandung bs_2.",
            font=("Arial", 10),
            bg="#f4f6f8",
            fg="#566573",
        )
        subtitle.pack(pady=(0, 8))

        frame_top = tk.Frame(self.root, bg="#f4f6f8")
        frame_top.pack(fill=tk.X, padx=16, pady=8)

        frame_input = tk.LabelFrame(
            frame_top,
            text="Input Scanner",
            font=("Arial", 10, "bold"),
            bg="#f4f6f8",
            padx=10,
            pady=10,
        )
        frame_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        tk.Label(
            frame_input,
            text="Scan barcode di sini:",
            font=("Arial", 11),
            bg="#f4f6f8",
        ).grid(row=0, column=0, sticky="w")

        self.entry_barcode = tk.Entry(
            frame_input,
            font=("Consolas", 18, "bold"),
            width=42,
            relief=tk.SOLID,
            bd=1,
        )
        self.entry_barcode.grid(row=1, column=0, sticky="ew", pady=(6, 4))

        self.entry_barcode.bind("<Return>", self.on_enter_pressed)
        self.entry_barcode.bind("<KeyRelease>", self.on_key_release)
        self.entry_barcode.bind("<FocusOut>", self.on_focus_out)

        frame_input.grid_columnconfigure(0, weight=1)

        self.label_hint = tk.Label(
            frame_input,
            text=f"Mode kuat fokus aktif. Jika scanner tidak punya ENTER, input diproses otomatis setelah idle {BARCODE_IDLE_FINISH_SECONDS} detik.",
            font=("Arial", 9),
            bg="#f4f6f8",
            fg="#566573",
            anchor="w",
            justify=tk.LEFT,
        )
        self.label_hint.grid(row=2, column=0, sticky="ew", pady=(2, 0))

        frame_status = tk.LabelFrame(
            frame_top,
            text="Status Sistem",
            font=("Arial", 10, "bold"),
            bg="#f4f6f8",
            padx=10,
            pady=10,
        )
        frame_status.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        self.label_state = tk.Label(
            frame_status,
            text="MENUNGGU SCAN 1",
            font=("Arial", 15, "bold"),
            bg="#1f8f4d",
            fg="white",
            width=24,
            height=2,
        )
        self.label_state.pack(pady=(0, 8))

        self.label_expected = tk.Label(
            frame_status,
            text="Expected: -",
            font=("Arial", 10),
            bg="#f4f6f8",
            fg="#17202a",
            anchor="w",
            justify=tk.LEFT,
        )
        self.label_expected.pack(fill=tk.X)

        self.label_relay = tk.Label(
            frame_status,
            text="Relay: OFF",
            font=("Arial", 10),
            bg="#f4f6f8",
            fg="#17202a",
            anchor="w",
            justify=tk.LEFT,
        )
        self.label_relay.pack(fill=tk.X)

        frame_buttons = tk.Frame(self.root, bg="#f4f6f8")
        frame_buttons.pack(fill=tk.X, padx=16, pady=(0, 8))

        self.btn_reset = tk.Button(
            frame_buttons,
            text="RESET SISTEM",
            font=("Arial", 11, "bold"),
            bg="#f39c12",
            fg="black",
            command=self.handle_reset,
            width=16,
        )
        self.btn_reset.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            frame_buttons,
            text="Export Rekap CSV",
            font=("Arial", 10),
            command=self.export_rekap,
            width=18,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            frame_buttons,
            text="Reset Tampilan",
            font=("Arial", 10),
            command=self.reset_tampilan,
            width=16,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            frame_buttons,
            text="Keluar",
            font=("Arial", 10),
            command=self.on_close,
            width=12,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        columns = (
            "waktu",
            "tahap",
            "barcode",
            "kode",
            "expected",
            "hasil",
        )

        self.table = ttk.Treeview(
            self.root,
            columns=columns,
            show="headings",
            height=12,
        )

        self.table.heading("waktu", text="Waktu")
        self.table.heading("tahap", text="Tahap")
        self.table.heading("barcode", text="Barcode")
        self.table.heading("kode", text="Kode")
        self.table.heading("expected", text="Expected")
        self.table.heading("hasil", text="Hasil")

        self.table.column("waktu", width=145, anchor=tk.CENTER)
        self.table.column("tahap", width=80, anchor=tk.CENTER)
        self.table.column("barcode", width=360)
        self.table.column("kode", width=80, anchor=tk.CENTER)
        self.table.column("expected", width=90, anchor=tk.CENTER)
        self.table.column("hasil", width=130, anchor=tk.CENTER)

        self.table.pack(padx=16, pady=(0, 8), fill=tk.BOTH, expand=True)

        frame_log = tk.LabelFrame(
            self.root,
            text="Log Sistem",
            font=("Arial", 10, "bold"),
            bg="#f4f6f8",
        )
        frame_log.pack(fill=tk.BOTH, padx=16, pady=(0, 12))

        self.text_log = tk.Text(
            frame_log,
            height=7,
            font=("Consolas", 9),
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.text_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(frame_log, command=self.text_log.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_log.config(yscrollcommand=scrollbar.set)

        self.label_status = tk.Label(
            self.root,
            text="Siap scan barcode.",
            font=("Arial", 10, "bold"),
            bg="#eafaf1",
            fg="#1e8449",
            anchor="w",
            padx=10,
        )
        self.label_status.pack(fill=tk.X, side=tk.BOTTOM)

        self.root.bind("<Button-1>", lambda event: self.root.after(50, self.refocus_entry))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -----------------------------------------------------
    # FOKUS INPUT BARCODE
    # -----------------------------------------------------
    def refocus_entry(self):
        try:
            if self.root.winfo_exists():
                self.entry_barcode.focus_force()
                self.entry_barcode.icursor(tk.END)
        except Exception:
            pass

    def on_focus_out(self, event=None):
        # Scanner HID perlu entry tetap aktif.
        # Delay kecil supaya klik tombol tetap sempat diproses.
        self.root.after(80, self.refocus_entry)

    def schedule_focus_guard(self):
        self.refocus_entry()
        self.focus_after_id = self.root.after(600, self.schedule_focus_guard)

    # -----------------------------------------------------
    # INPUT SCANNER
    # -----------------------------------------------------
    def on_key_release(self, event):
        # Jika scanner mengirim ENTER, Return akan diproses oleh on_enter_pressed.
        if event.keysym in ("Return", "KP_Enter"):
            return

        if self.idle_after_id is not None:
            self.root.after_cancel(self.idle_after_id)

        # Jika scanner tidak punya suffix ENTER, input selesai ketika idle.
        self.idle_after_id = self.root.after(BARCODE_IDLE_FINISH_MS, self.finish_input_from_entry)

    def on_enter_pressed(self, event=None):
        if self.idle_after_id is not None:
            self.root.after_cancel(self.idle_after_id)
            self.idle_after_id = None

        self.finish_input_from_entry()
        return "break"

    def finish_input_from_entry(self):
        self.idle_after_id = None

        barcode = self.entry_barcode.get().strip()

        if barcode == "":
            self.refocus_entry()
            return

        self.entry_barcode.delete(0, tk.END)
        self.refocus_entry()

        self.process_barcode(barcode)

    # -----------------------------------------------------
    # JUDGMENT UTAMA
    # -----------------------------------------------------
    def process_barcode(self, barcode):
        waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if barcode in RESET_CODES:
            self.insert_scan_row(waktu, "RESET", barcode, get_display_code(barcode), "-", "RESET")
            self.simpan_scan(waktu, "RESET", barcode, get_display_code(barcode), "-", "RESET")
            self.handle_reset()
            return

        if self.system_locked or self.state == self.STATE_LOCKED or is_any_alarm_active():
            self.insert_scan_row(waktu, "LOCKED", barcode, get_display_code(barcode), self.expected_scan_2_code or "-", "DIABAIKAN")
            self.simpan_scan(waktu, "LOCKED", barcode, get_display_code(barcode), self.expected_scan_2_code or "-", "DIABAIKAN")
            self.set_status("Sistem terkunci karena NG atau timeout. Scan reset terlebih dahulu.", "error")
            self.write_log("Sistem terkunci. Input selain reset diabaikan.")
            return

        if self.state == self.STATE_SCAN_1:
            self.handle_scan_1(barcode, waktu)
            return

        if self.state == self.STATE_SCAN_2:
            self.handle_scan_2(barcode, waktu)
            return

    def handle_scan_1(self, scan_1, waktu):
        scan_1_code = get_product_balance_code(scan_1)

        if scan_1_code not in VALID_SCAN1_CODES:
            self.insert_scan_row(waktu, "SCAN 1", scan_1, scan_1_code, "-", "INVALID")
            self.simpan_scan(waktu, "SCAN 1", scan_1, scan_1_code, "-", "INVALID")
            self.set_status("Scan 1 tidak valid. Barcode scan 1 harus mengandung 13621 atau 13622.", "warning")
            self.write_log(f"Scan 1 tidak valid: {scan_1}")
            self.update_rekap(scan_1, waktu)
            return

        self.scan_1_raw = scan_1
        self.scan_1_code = scan_1_code
        self.expected_scan_2_code = SCAN1_PRODUCT_CODE_TO_EXPECTED_SCAN2_JUDGMENT_CODE[scan_1_code]
        self.expected_relay_pin = EXPECTED_SCAN2_CODE_TO_GPIO[self.expected_scan_2_code]
        self.scan_1_time_monotonic = time.monotonic()

        self.state = self.STATE_SCAN_2
        self.system_locked = False

        self.insert_scan_row(waktu, "SCAN 1", scan_1, scan_1_code, self.expected_scan_2_code, "DITERIMA")
        self.simpan_scan(waktu, "SCAN 1", scan_1, scan_1_code, self.expected_scan_2_code, "DITERIMA")
        self.update_rekap(scan_1, waktu)

        start_relay_blink(self.expected_relay_pin)

        self.set_status(
            f"Scan 1 diterima. Scan 2 wajib kode {self.expected_scan_2_code} maksimal {SCAN_2_TIMEOUT_SECONDS} detik.",
            "info",
        )
        self.write_log(f"Scan 1 diterima: {scan_1}")
        self.write_log(f"Acuan scan 1: {scan_1_code}")
        self.write_log(f"Scan 2 yang harus sesuai: {self.expected_scan_2_code}")
        self.write_log(f"GPIO {self.expected_relay_pin} berkedip sampai scan 2 sesuai.")

        self.update_state_display()
        self.start_scan_2_timeout()

    def handle_scan_2(self, scan_2, waktu):
        now = time.monotonic()

        if (
            scan_2 == self.scan_1_raw
            and now - self.scan_1_time_monotonic <= IGNORE_DUPLICATE_SCAN1_SECONDS
        ):
            self.insert_scan_row(waktu, "SCAN 2", scan_2, get_display_code(scan_2), self.expected_scan_2_code, "DUPLIKAT")
            self.simpan_scan(waktu, "SCAN 2", scan_2, get_display_code(scan_2), self.expected_scan_2_code, "DUPLIKAT")
            self.set_status("Scan duplikat dari scan 1 terdeteksi sangat cepat. Diabaikan.", "warning")
            self.write_log("Scan duplikat dari scan 1 terdeteksi sangat cepat. Diabaikan.")
            return

        self.cancel_scan_2_timeout()

        scan_2_code = get_judgment_balance_code(scan_2)
        self.update_rekap(scan_2, waktu)

        if scan_2_code == self.expected_scan_2_code:
            self.insert_scan_row(waktu, "SCAN 2", scan_2, scan_2_code, self.expected_scan_2_code, "OK")
            self.simpan_scan(waktu, "SCAN 2", scan_2, scan_2_code, self.expected_scan_2_code, "OK")

            self.write_log(f"Scan 2 diterima: {scan_2}")
            self.write_log(f"Acuan scan 2: {scan_2_code}")
            self.write_log("HASIL: OK")

            stop_relay_blink()
            turn_off_all_relays()

            self.reset_scan_state_only()
            self.set_status("HASIL OK. Relay OFF. Silakan scan urutan 1 berikutnya.", "success")
            self.update_state_display()
            return

        self.insert_scan_row(waktu, "SCAN 2", scan_2, scan_2_code, self.expected_scan_2_code, "NG")
        self.simpan_scan(waktu, "SCAN 2", scan_2, scan_2_code, self.expected_scan_2_code, "NG")

        self.state = self.STATE_LOCKED
        self.system_locked = True

        self.write_log(f"Scan 2 diterima: {scan_2}")
        self.write_log(f"Acuan scan 2: {scan_2_code}")
        self.write_log("HASIL: NG / TIDAK SESUAI")
        self.write_log("ALARM AKTIF. Relay tetap berkedip sampai reset discan.")

        play_alarm_loop()

        self.set_status("HASIL NG. Sistem terkunci. Scan reset untuk mematikan alarm dan relay.", "error")
        self.update_state_display()

    def start_scan_2_timeout(self):
        self.cancel_scan_2_timeout()
        self.timeout_after_id = self.root.after(
            SCAN_2_TIMEOUT_SECONDS * 1000,
            self.handle_scan_2_timeout,
        )

    def cancel_scan_2_timeout(self):
        if self.timeout_after_id is not None:
            try:
                self.root.after_cancel(self.timeout_after_id)
            except Exception:
                pass
            self.timeout_after_id = None

    def handle_scan_2_timeout(self):
        self.timeout_after_id = None

        if self.state != self.STATE_SCAN_2:
            return

        waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.state = self.STATE_LOCKED
        self.system_locked = True

        self.insert_scan_row(waktu, "SCAN 2", "-", "-", self.expected_scan_2_code, "TIMEOUT")
        self.simpan_scan(waktu, "SCAN 2", "-", "-", self.expected_scan_2_code, "TIMEOUT")

        self.write_log(f"TIMEOUT. Scan 2 tidak diterima dalam {SCAN_2_TIMEOUT_SECONDS} detik.")
        self.write_log("HASIL: NG / TIMEOUT")
        self.write_log("ALARM TIMEOUT AKTIF. Relay tetap berkedip sampai reset discan.")

        start_timeout_alarm_sequence()

        self.set_status("TIMEOUT SCAN 2. Sistem terkunci. Scan reset terlebih dahulu.", "error")
        self.update_state_display()

    # -----------------------------------------------------
    # RESET
    # -----------------------------------------------------
    def handle_reset(self):
        self.cancel_scan_2_timeout()

        self.system_locked = False
        self.reset_scan_state_only()

        stop_timeout_alarm_sequence()
        stop_all_sound()
        stop_relay_blink()
        turn_off_all_relays()

        self.set_status("RESET OK. Semua alarm dan relay dimatikan. Sistem kembali menunggu scan 1.", "success")
        self.write_log("RESET OK. Semua alarm dan relay dimatikan.")
        self.update_state_display()
        self.refocus_entry()

    def reset_scan_state_only(self):
        self.state = self.STATE_SCAN_1
        self.scan_1_raw = ""
        self.scan_1_code = ""
        self.expected_scan_2_code = ""
        self.expected_relay_pin = None
        self.scan_1_time_monotonic = 0.0

    # -----------------------------------------------------
    # DATA DAN TABEL
    # -----------------------------------------------------
    def simpan_scan(self, waktu, tahap, barcode, kode, expected, hasil):
        file_baru = not FILE_SCAN.exists()

        with open(FILE_SCAN, mode="a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)

            if file_baru:
                writer.writerow(["waktu_scan", "tahap", "barcode", "kode", "expected", "hasil"])

            writer.writerow([waktu, tahap, barcode, kode, expected, hasil])

    def load_existing_data(self):
        if not FILE_SCAN.exists():
            return

        try:
            with open(FILE_SCAN, mode="r", newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)

                for row in reader:
                    waktu = row.get("waktu_scan", "").strip()
                    tahap = row.get("tahap", "").strip()
                    barcode = row.get("barcode", "").strip()
                    kode = row.get("kode", "").strip()
                    expected = row.get("expected", "").strip()
                    hasil = row.get("hasil", "").strip()

                    if barcode and barcode != "-":
                        self.update_rekap(barcode, waktu)

                    self.insert_scan_row(waktu, tahap, barcode, kode, expected, hasil, scroll=False)

        except Exception as e:
            messagebox.showerror("Error", f"Gagal membaca data lama:\n{e}")

    def update_rekap(self, barcode, waktu):
        if not barcode or barcode == "-":
            return

        if barcode in self.data_rekap:
            self.data_rekap[barcode]["jumlah"] += 1
            self.data_rekap[barcode]["scan_terakhir"] = waktu
        else:
            self.data_rekap[barcode] = {
                "jumlah": 1,
                "scan_terakhir": waktu,
            }

    def insert_scan_row(self, waktu, tahap, barcode, kode, expected, hasil, scroll=True):
        item = self.table.insert(
            "",
            tk.END,
            values=(waktu, tahap, barcode, kode, expected, hasil),
        )

        if scroll:
            self.table.see(item)

    def export_rekap(self):
        try:
            with open(FILE_REKAP, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["barcode", "jumlah", "scan_terakhir"])

                for barcode, info in sorted(self.data_rekap.items()):
                    writer.writerow([
                        barcode,
                        info["jumlah"],
                        info["scan_terakhir"],
                    ])

            messagebox.showinfo(
                "Berhasil",
                f"Rekap berhasil disimpan ke file:\n{FILE_REKAP}",
            )

        except Exception as e:
            messagebox.showerror("Error", f"Gagal export rekap:\n{e}")

        self.refocus_entry()

    def reset_tampilan(self):
        konfirmasi = messagebox.askyesno(
            "Konfirmasi",
            "Reset hanya menghapus tampilan tabel dan rekap, bukan file data scan. Lanjutkan?",
        )

        if konfirmasi:
            self.data_rekap.clear()

            for item in self.table.get_children():
                self.table.delete(item)

            self.set_status("Tampilan tabel dan rekap sudah direset.", "warning")

        self.refocus_entry()

    # -----------------------------------------------------
    # STATUS DAN LOG
    # -----------------------------------------------------
    def set_status(self, text, status_type="info"):
        color_map = {
            "success": ("#eafaf1", "#1e8449"),
            "info": ("#ebf5fb", "#21618c"),
            "warning": ("#fff8e1", "#b9770e"),
            "error": ("#fdecea", "#c0392b"),
        }

        bg, fg = color_map.get(status_type, color_map["info"])
        self.label_status.config(text=text, bg=bg, fg=fg)

    def write_log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}\n"

        log_console(text)

        self.text_log.config(state=tk.NORMAL)
        self.text_log.insert(tk.END, line)
        self.text_log.see(tk.END)
        self.text_log.config(state=tk.DISABLED)

    def update_state_display(self):
        if self.state == self.STATE_SCAN_1:
            self.label_state.config(text="MENUNGGU SCAN 1", bg="#1f8f4d")
            self.label_expected.config(text="Expected: -")
            self.label_relay.config(text="Relay: OFF")
            return

        if self.state == self.STATE_SCAN_2:
            self.label_state.config(text="MENUNGGU SCAN 2", bg="#21618c")
            self.label_expected.config(text=f"Expected scan 2: {self.expected_scan_2_code}")
            self.label_relay.config(text=f"Relay: GPIO {self.expected_relay_pin} berkedip")
            return

        if self.state == self.STATE_LOCKED:
            self.label_state.config(text="LOCKED / NG", bg="#c0392b")
            self.label_expected.config(text=f"Expected scan 2: {self.expected_scan_2_code or '-'}")
            self.label_relay.config(text=f"Relay: GPIO {self.expected_relay_pin or '-'} tetap berkedip")
            return

    def log_startup(self):
        self.write_log("=== PROGRAM SCAN QR + GPIO RELAY + GUI ===")
        self.write_log("Program mulai dijalankan.")
        self.write_log(f"Scan reset: {', '.join(sorted(RESET_CODES))}")
        self.write_log(f"Timeout scan 2: {SCAN_2_TIMEOUT_SECONDS} detik")
        self.write_log(f"Anti duplikat scan 1: {IGNORE_DUPLICATE_SCAN1_SECONDS} detik")
        self.write_log(f"Idle finish barcode: {BARCODE_IDLE_FINISH_SECONDS} detik")
        self.write_log("Acuan judgment scan 2 memakai kode bs_1 / bs_2.")
        self.write_log("Balance Shaft 1 / 13621 -> scan 2 wajib bs_1")
        self.write_log("Balance Shaft 2 / 13622 -> scan 2 wajib bs_2")
        self.write_log("GPIO 21 aktif berkedip jika scan 2 yang diharapkan: bs_1")
        self.write_log("GPIO 20 aktif berkedip jika scan 2 yang diharapkan: bs_2")
        self.write_log(f"Folder Media: {MEDIA_DIR}")
        self.write_log(f"Data scan: {FILE_SCAN}")
        self.update_state_display()

    # -----------------------------------------------------
    # CLOSE
    # -----------------------------------------------------
    def on_close(self):
        self.cancel_scan_2_timeout()

        if self.idle_after_id is not None:
            try:
                self.root.after_cancel(self.idle_after_id)
            except Exception:
                pass
            self.idle_after_id = None

        cleanup()
        self.root.destroy()


def main():
    setup_gpio()
    setup_audio()
    turn_off_all_relays()

    root = tk.Tk()
    app = BarcodeJudgmentApp(root)

    # Guard fokus dibuat setelah UI siap agar entry barcode terus aktif.
    app.schedule_focus_guard()

    root.mainloop()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        cleanup()
        print("\nProgram dihentikan.", flush=True)

    except Exception as e:
        cleanup()
        print("\nPROGRAM ERROR:", e, flush=True)
        raise
