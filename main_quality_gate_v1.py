# =========================================================
# SISTEM POKA-YOKE INDUSTRIAL FINAL VERSION
# Raspberry Pi + EVDEV + 3 Barcode Scanner (BY-PATH LOCKED)
# FULLSCREEN + UI + ERROR LOCK + S1 OVERRIDE
# SPAM BYPASS + IGNORE UNKNOWN + ID CARD RESET SCAN
# =========================================================

from pathlib import Path
import csv
import time
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

# =========================================================
# EVDEV
# =========================================================
import evdev
from evdev import InputDevice, ecodes

# =========================================================
# OPTIONAL AUDIO
# =========================================================
try:
    import pygame
except:
    pygame = None

# =========================================================
# OPTIONAL GPIO
# =========================================================
try:
    import RPi.GPIO as GPIO
except:
    GPIO = None

# =========================================================
# SCANNER DEVICE
# Mengunci urutan scanner berdasarkan port USB fisik
# =========================================================
SCANNER_1_PATH = "/dev/input/by-path/platform-3f980000.usb-usb-0:1.1.2:1.0-event-kbd"
SCANNER_2_PATH = "/dev/input/by-path/platform-3f980000.usb-usb-0:1.1.3:1.0-event-kbd"
SCANNER_3_PATH = "/dev/input/by-path/platform-3f980000.usb-usb-0:1.2:1.0-event-kbd"

# =========================================================
# PRODUCT CODE
# =========================================================
PRODUCT_13621 = "13621"
PRODUCT_13622 = "13622"

TARGET_SCANNER = {
    PRODUCT_13621: "S2",
    PRODUCT_13622: "S3"
}

# =========================================================
# RESET ID CARD
# =========================================================
RESET_CODES = {
    "oi-234066", "oi-233948", "oi-010145", "oi-060760",
    "oi-99070", "oi-00103", "oi-213801", "oi-234036",
    "oi-mg260907", "oi-990070"
}

# =========================================================
# GPIO PINS & TIMEOUT
# =========================================================
GPIO_13621 = 20
GPIO_13622 = 21

SCAN_2_TIMEOUT_SECONDS = 77
BLINK_INTERVAL_SECONDS = 0.5

# =========================================================
# SETTING TOLERANSI DOUBLE-SCAN
# =========================================================
DOUBLE_SCAN_TOLERANCE_SECONDS = 20.0
MAX_SPAM_TOLERANCE = 20

# =========================================================
# PATH & CSV
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / "history_scan.csv"

MEDIA_DIR = BASE_DIR / "Media"
SOUND_ALARM_PATH = MEDIA_DIR / "0020.wav"
SOUND_SCAN_2_TIMEOUT_PATH = MEDIA_DIR / "0020.wav"

# =========================================================
# AUDIO MANAGEMENT
# =========================================================
AUDIO_READY = False
ALARM_CHANNEL = None
VOICE_CHANNEL = None
SOUND_ALARM = None
SOUND_SCAN_2_TIMEOUT = None


def load_sound(file_path):
    if not AUDIO_READY:
        return None

    if not file_path.exists():
        return None

    try:
        return pygame.mixer.Sound(str(file_path))
    except:
        return None


def setup_audio():
    global AUDIO_READY
    global ALARM_CHANNEL
    global VOICE_CHANNEL
    global SOUND_ALARM
    global SOUND_SCAN_2_TIMEOUT

    if pygame is None:
        return

    try:
        pygame.mixer.init()
        pygame.mixer.set_num_channels(4)

        ALARM_CHANNEL = pygame.mixer.Channel(0)
        VOICE_CHANNEL = pygame.mixer.Channel(1)

        AUDIO_READY = True
    except:
        return

    SOUND_ALARM = load_sound(SOUND_ALARM_PATH)
    SOUND_SCAN_2_TIMEOUT = load_sound(SOUND_SCAN_2_TIMEOUT_PATH)


def stop_all_sound():
    if AUDIO_READY:
        pygame.mixer.stop()


def play_alarm_loop():
    if not AUDIO_READY:
        return

    if SOUND_ALARM is None:
        return

    if ALARM_CHANNEL is None:
        return

    ALARM_CHANNEL.stop()
    ALARM_CHANNEL.play(SOUND_ALARM, loops=-1)


timeout_alarm_stop_event = threading.Event()
timeout_alarm_thread = None


def timeout_alarm_worker():
    if not AUDIO_READY:
        return

    if SOUND_SCAN_2_TIMEOUT is None:
        return

    if SOUND_ALARM is None:
        return

    while not timeout_alarm_stop_event.is_set():
        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()

        VOICE_CHANNEL.play(SOUND_SCAN_2_TIMEOUT)

        while VOICE_CHANNEL.get_busy() and not timeout_alarm_stop_event.is_set():
            time.sleep(0.05)

        if timeout_alarm_stop_event.wait(0.15):
            break

        ALARM_CHANNEL.play(SOUND_ALARM)

        while ALARM_CHANNEL.get_busy() and not timeout_alarm_stop_event.is_set():
            time.sleep(0.05)

        if timeout_alarm_stop_event.wait(0.15):
            break


def start_timeout_alarm_sequence():
    global timeout_alarm_thread

    stop_timeout_alarm_sequence()

    timeout_alarm_stop_event.clear()
    timeout_alarm_thread = threading.Thread(
        target=timeout_alarm_worker,
        daemon=True
    )
    timeout_alarm_thread.start()


def stop_timeout_alarm_sequence():
    global timeout_alarm_thread

    timeout_alarm_stop_event.set()

    if AUDIO_READY:
        if ALARM_CHANNEL:
            ALARM_CHANNEL.stop()

        if VOICE_CHANNEL:
            VOICE_CHANNEL.stop()

    if timeout_alarm_thread:
        timeout_alarm_thread.join(timeout=0.2)

    timeout_alarm_thread = None


# =========================================================
# GPIO BLINK MANAGEMENT
# =========================================================
relay_blink_stop_event = threading.Event()
relay_blink_thread = None

if GPIO:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    GPIO.setup(GPIO_13621, GPIO.OUT)
    GPIO.setup(GPIO_13622, GPIO.OUT)

    GPIO.output(GPIO_13621, GPIO.LOW)
    GPIO.output(GPIO_13622, GPIO.LOW)


def relay_blink_worker(pin):
    while not relay_blink_stop_event.is_set():
        if GPIO:
            GPIO.output(pin, GPIO.HIGH)

        if relay_blink_stop_event.wait(BLINK_INTERVAL_SECONDS):
            break

        if GPIO:
            GPIO.output(pin, GPIO.LOW)

        if relay_blink_stop_event.wait(BLINK_INTERVAL_SECONDS):
            break

    if GPIO:
        GPIO.output(pin, GPIO.LOW)


def start_relay_blink(pin):
    global relay_blink_thread

    stop_relay_blink()

    relay_blink_stop_event.clear()
    relay_blink_thread = threading.Thread(
        target=relay_blink_worker,
        args=(pin,),
        daemon=True
    )
    relay_blink_thread.start()


def stop_relay_blink():
    global relay_blink_thread

    relay_blink_stop_event.set()

    if relay_blink_thread:
        relay_blink_thread.join(timeout=0.2)

    relay_blink_thread = None

    if GPIO:
        GPIO.output(GPIO_13621, GPIO.LOW)
        GPIO.output(GPIO_13622, GPIO.LOW)


# =========================================================
# KEYMAP
# =========================================================
KEYMAP = {
    2: '1',
    3: '2',
    4: '3',
    5: '4',
    6: '5',
    7: '6',
    8: '7',
    9: '8',
    10: '9',
    11: '0',
    12: '-',

    16: 'q',
    17: 'w',
    18: 'e',
    19: 'r',
    20: 't',
    21: 'y',
    22: 'u',
    23: 'i',
    24: 'o',
    25: 'p',

    30: 'a',
    31: 's',
    32: 'd',
    33: 'f',
    34: 'g',
    35: 'h',
    36: 'j',
    37: 'k',
    38: 'l',

    44: 'z',
    45: 'x',
    46: 'c',
    47: 'v',
    48: 'b',
    49: 'n',
    50: 'm'
}


# =========================================================
# MAIN CLASS
# =========================================================
class PokaYokeSystem:

    def __init__(self, root):
        self.root = root

        # FULLSCREEN
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", self.exit_fullscreen)
        self.root.bind("<F11>", self.toggle_fullscreen)

        self.root.title("POKA YOKE SYSTEM")
        self.root.configure(bg="#f3f4f6")

        # =================================================
        # STATE & VARIABLES
        # =================================================
        self.status = "WAIT_START"
        self.expected_part = ""
        self.expected_scanner = ""
        self.running = True
        self.timeout_after_id = None

        # Memori S1 agar scan yang sama tidak langsung berulang tanpa reset
        self.current_s1_barcode = ""

        # Debounce S2/S3
        self.last_scan_time = 0.0
        self.last_scan_data = ""
        self.last_scanner = ""
        self.spam_count = 0

        # BUILD APP
        self.setup_style()
        self.build_ui()

        # THREAD SCANNER
        threading.Thread(
            target=self.evdev_worker,
            args=(SCANNER_1_PATH, "S1"),
            daemon=True
        ).start()

        threading.Thread(
            target=self.evdev_worker,
            args=(SCANNER_2_PATH, "S2"),
            daemon=True
        ).start()

        threading.Thread(
            target=self.evdev_worker,
            args=(SCANNER_3_PATH, "S3"),
            daemon=True
        ).start()

        self.write_log("SYSTEM READY. FULL PROTECTIONS AKTIF.")
        self.write_log("RESET ID CARD AKTIF UNTUK RESET SCAN ULANG.")

    # =====================================================
    # STYLE
    # =====================================================
    def setup_style(self):
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except:
            pass

        style.configure(
            "Treeview",
            background="#ffffff",
            foreground="#111827",
            fieldbackground="#ffffff",
            rowheight=34,
            bordercolor="#d1d5db",
            borderwidth=1,
            font=("Segoe UI", 11)
        )

        style.map(
            "Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", "#111827")]
        )

        style.configure(
            "Treeview.Heading",
            background="#e5e7eb",
            foreground="#111827",
            relief="flat",
            font=("Segoe UI", 11, "bold")
        )

    # =====================================================
    # UI
    # =====================================================
    def build_ui(self):
        self.COLOR_BG = "#f3f4f6"
        self.COLOR_HEADER = "#111827"
        self.COLOR_PANEL = "#ffffff"
        self.COLOR_BORDER = "#d1d5db"
        self.COLOR_TEXT = "#111827"
        self.COLOR_MUTED = "#4b5563"

        self.COLOR_STATUS_WAIT = "#e5e7eb"
        self.COLOR_STATUS_ACTIVE = "#1f2937"
        self.COLOR_STATUS_SUCCESS = "#166534"
        self.COLOR_STATUS_ERROR = "#991b1b"

        header = tk.Frame(
            self.root,
            bg=self.COLOR_HEADER,
            height=78
        )
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        title = tk.Label(
            header,
            text="SISTEM POKA-YOKE INDUSTRIAL",
            font=("Segoe UI", 24, "bold"),
            bg=self.COLOR_HEADER,
            fg="#ffffff"
        )
        title.pack(anchor="w", padx=28, pady=(12, 0))

        subtitle = tk.Label(
            header,
            text="Production Validation Monitor",
            font=("Segoe UI", 11),
            bg=self.COLOR_HEADER,
            fg="#cbd5e1"
        )
        subtitle.pack(anchor="w", padx=30, pady=(0, 10))

        status_frame = tk.Frame(
            self.root,
            bg=self.COLOR_PANEL,
            height=170,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1
        )
        status_frame.pack(fill=tk.X, padx=24, pady=(22, 14))
        status_frame.pack_propagate(False)

        self.label_status = tk.Label(
            status_frame,
            text="MENUNGGU SCAN AWAL",
            font=("Segoe UI", 28, "bold"),
            bg=self.COLOR_STATUS_WAIT,
            fg=self.COLOR_TEXT,
            pady=18
        )
        self.label_status.pack(fill=tk.X, padx=18, pady=(18, 12))

        info_frame = tk.Frame(
            status_frame,
            bg=self.COLOR_PANEL
        )
        info_frame.pack(fill=tk.X, padx=20)

        self.label_target = tk.Label(
            info_frame,
            text="Target Part : -",
            font=("Segoe UI", 14),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_MUTED
        )
        self.label_target.pack(side=tk.LEFT)

        self.label_mode = tk.Label(
            info_frame,
            text="Mode : WAIT_START",
            font=("Segoe UI", 14),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_MUTED
        )
        self.label_mode.pack(side=tk.RIGHT)

        scanner_frame = tk.Frame(
            self.root,
            bg=self.COLOR_BG
        )
        scanner_frame.pack(fill=tk.X, padx=24, pady=(0, 8))

        self.create_scanner_card(
            scanner_frame,
            "SCANNER 1",
            "Start Scanner"
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))

        self.create_scanner_card(
            scanner_frame,
            "SCANNER 2",
            "Judgment Line S2"
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=8)

        self.create_scanner_card(
            scanner_frame,
            "SCANNER 3",
            "Judgment Line S3"
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(8, 0))

        table_frame = tk.Frame(
            self.root,
            bg=self.COLOR_PANEL,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1
        )
        table_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=12)

        columns = ("time", "scanner", "barcode", "phase", "result")

        self.table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings"
        )

        self.table.heading("time", text="TIME")
        self.table.heading("scanner", text="SCANNER")
        self.table.heading("barcode", text="BARCODE")
        self.table.heading("phase", text="PHASE")
        self.table.heading("result", text="RESULT")

        self.table.column("time", width=220, anchor="center")
        self.table.column("scanner", width=120, anchor="center")
        self.table.column("barcode", width=260, anchor="w")
        self.table.column("phase", width=220, anchor="center")
        self.table.column("result", width=120, anchor="center")

        self.table.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.text_log = tk.Text(
            self.root,
            height=7,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief="flat",
            font=("Consolas", 11)
        )
        self.text_log.pack(fill=tk.X, padx=24, pady=(0, 12))

        button_frame = tk.Frame(
            self.root,
            bg=self.COLOR_BG
        )
        button_frame.pack(fill=tk.X, padx=24, pady=(0, 18))

        self.create_button(
            button_frame,
            "RESET UI",
            self.reset_ui
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.create_button(
            button_frame,
            "RESET SCAN",
            self.reset_scan_cycle
        ).pack(side=tk.LEFT, padx=10)

        self.create_button(
            button_frame,
            "EXPORT CSV",
            self.export_csv
        ).pack(side=tk.LEFT, padx=10)

        self.create_button(
            button_frame,
            "EXIT",
            self.close_program,
            danger=True
        ).pack(side=tk.RIGHT)

    def create_scanner_card(self, parent, title, subtitle):
        frame = tk.Frame(
            parent,
            bg=self.COLOR_PANEL,
            height=95,
            highlightbackground=self.COLOR_BORDER,
            highlightthickness=1
        )
        frame.pack_propagate(False)

        label = tk.Label(
            frame,
            text=title,
            font=("Segoe UI", 16, "bold"),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_TEXT
        )
        label.pack(anchor="w", padx=18, pady=(16, 2))

        desc = tk.Label(
            frame,
            text=subtitle,
            font=("Segoe UI", 11),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_MUTED
        )
        desc.pack(anchor="w", padx=18)

        line = tk.Frame(
            frame,
            bg="#334155",
            height=3
        )
        line.pack(fill=tk.X, side=tk.BOTTOM)

        return frame

    def create_button(self, parent, text, command, danger=False):
        bg = "#7f1d1d" if danger else "#1f2937"
        active_bg = "#991b1b" if danger else "#374151"

        return tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 12, "bold"),
            bg=bg,
            fg="#ffffff",
            activebackground=active_bg,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            width=16,
            height=2,
            cursor="hand2",
            command=command
        )

    # =====================================================
    # TIMEOUT MANAGEMENT
    # =====================================================
    def start_scan_2_timeout(self):
        self.cancel_scan_2_timeout()

        self.timeout_after_id = self.root.after(
            SCAN_2_TIMEOUT_SECONDS * 1000,
            self.handle_timeout_violation
        )

    def cancel_scan_2_timeout(self):
        if self.timeout_after_id:
            try:
                self.root.after_cancel(self.timeout_after_id)
            except:
                pass

            self.timeout_after_id = None

    def handle_timeout_violation(self):
        self.timeout_after_id = None

        if self.status != "WAIT_JUDGMENT":
            return

        self.insert_table("SYSTEM", "-", "TIMEOUT_JUDGMENT", "NG")
        self.error_lock("TIMEOUT ! MESIN TERKUNCI")
        start_timeout_alarm_sequence()

    # =====================================================
    # FULLSCREEN
    # =====================================================
    def toggle_fullscreen(self, event=None):
        current = self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", not current)

    def exit_fullscreen(self, event=None):
        self.root.attributes("-fullscreen", False)

    # =====================================================
    # LOG
    # =====================================================
    def write_log(self, text):
        now = datetime.now().strftime("%H:%M:%S")
        final = f"[{now}] {text}\n"

        self.text_log.insert(tk.END, final)
        self.text_log.see(tk.END)

        print(final)

    # =====================================================
    # TABLE
    # =====================================================
    def insert_table(self, scanner, barcode, phase, result):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.table.insert(
            "",
            0,
            values=(now, scanner, barcode, phase, result)
        )

    # =====================================================
    # EVDEV
    # =====================================================
    def evdev_worker(self, device_path, scanner_id):
        try:
            device = InputDevice(device_path)
            buffer = ""

            for event in device.read_loop():
                if not self.running:
                    break

                if event.type == ecodes.EV_KEY and event.value == 1:
                    if event.code == ecodes.KEY_ENTER or event.code == ecodes.KEY_KPENTER:
                        if buffer:
                            barcode = buffer.lower()

                            self.root.after(
                                0,
                                self.process_barcode,
                                barcode,
                                scanner_id
                            )

                            buffer = ""
                    else:
                        char = KEYMAP.get(event.code, "")

                        if char:
                            buffer += char

        except Exception as e:
            self.write_log(f"{scanner_id} ERROR : {e}")

    # =====================================================
    # ERROR LOCK
    # =====================================================
    def error_lock(self, text):
        self.status = "ERROR_LOCK"

        self.label_status.config(
            text=text,
            bg=self.COLOR_STATUS_ERROR,
            fg="#ffffff"
        )

        self.label_mode.config(text="Mode : ERROR_LOCK")

        self.write_log("SYSTEM LOCKED")
        self.write_log("SCAN ID CARD FOR RESET")

        stop_relay_blink()

    # =====================================================
    # RESET SCAN MEMORY
    # =====================================================
    def clear_scan_memory(self):
        """
        Membersihkan seluruh memori scan.
        Ini yang membuat part yang sudah berhasil discan
        bisa discan ulang setelah reset berhasil.
        """

        # Memori barcode S1 dibersihkan
        self.current_s1_barcode = ""

        # Memori debounce S2/S3 dibersihkan
        self.last_scan_time = 0.0
        self.last_scan_data = ""
        self.last_scanner = ""
        self.spam_count = 0

    def reset_scan_cycle(self):
        """
        Reset siklus scan.
        Dipakai oleh tombol RESET SCAN dan ID Card reset.
        """

        self.cancel_scan_2_timeout()

        stop_all_sound()
        stop_timeout_alarm_sequence()
        stop_relay_blink()

        self.clear_scan_memory()
        self.reset_to_wait()

        self.label_status.config(
            text="RESET SCAN BERHASIL",
            bg=self.COLOR_STATUS_ACTIVE,
            fg="#ffffff"
        )

        self.write_log("RESET SCAN BERHASIL - DATA SCAN LAMA DIBERSIHKAN")

        self.root.after(
            1500,
            lambda: self.label_status.config(
                text="MENUNGGU SCAN AWAL",
                bg=self.COLOR_STATUS_WAIT,
                fg=self.COLOR_TEXT
            )
        )

    # =====================================================
    # PROCESS BARCODE
    # =====================================================
    def process_barcode(self, barcode, scanner):

        # =================================================
        # -1. FILTER GLOBAL
        # =================================================
        is_valid_part = (
            PRODUCT_13621 in barcode or
            PRODUCT_13622 in barcode
        )

        is_valid_reset = any(
            reset_code in barcode
            for reset_code in RESET_CODES
        )

        # Abaikan barcode yang tidak dikenal
        if not is_valid_part and not is_valid_reset:
            return

        # =================================================
        # -0.5. RESET ID CARD MODE
        # =================================================
        # Jika scan ID Card reset berhasil, sistem akan:
        # 1. membatalkan timeout,
        # 2. mematikan alarm,
        # 3. mematikan relay,
        # 4. membersihkan memori scan lama,
        # 5. mengembalikan status ke WAIT_START.
        #
        # Hasilnya:
        # part yang sudah pernah sukses discan bisa discan ulang.
        # =================================================
        if is_valid_reset:
            self.write_log(f"{scanner} -> RESET ID CARD VALID")
            self.insert_table(scanner, barcode, "SCAN_RESET", "OK")
            self.reset_scan_cycle()
            return

        # =================================================
        # 0. ERROR LOCK MODE
        # =================================================
        # Kalau mesin terkunci, hanya ID Card reset yang boleh membuka.
        # Karena ID Card reset sudah diproses di atas,
        # barcode part biasa akan ditolak.
        # =================================================
        if self.status == "ERROR_LOCK":
            self.write_log(f"AKSES DITOLAK : {barcode}")
            return

        # =================================================
        # 1. BYPASS SPAM STATIS S1
        # =================================================
        # Jika S1 membaca barcode yang sama lagi tanpa reset,
        # maka diabaikan.
        #
        # Tetapi setelah ID Card reset discan, current_s1_barcode
        # dikosongkan, sehingga barcode yang sama bisa dipakai ulang.
        # =================================================
        if scanner == "S1" and barcode == self.current_s1_barcode:
            return

        # =================================================
        # 2. PENYARING DOUBLE-SCAN S2/S3
        # =================================================
        current_time = time.time()

        if self.last_scanner == scanner and self.last_scan_data == barcode:
            if (current_time - self.last_scan_time) <= DOUBLE_SCAN_TOLERANCE_SECONDS:
                self.spam_count += 1

                if self.spam_count <= MAX_SPAM_TOLERANCE:
                    self.write_log(
                        f"[{scanner}] Abaikan spam "
                        f"({self.spam_count}/{MAX_SPAM_TOLERANCE})."
                    )
                    return
                else:
                    self.write_log(
                        f"[{scanner}] SPAM MELEBIHI BATAS! Memicu Error."
                    )
            else:
                self.spam_count = 0
        else:
            self.spam_count = 0

        self.last_scan_time = current_time
        self.last_scan_data = barcode
        self.last_scanner = scanner

        self.write_log(f"{scanner} -> {barcode}")

        # =================================================
        # 3. S1 OVERRIDE / START CYCLE
        # =================================================
        if scanner == "S1":
            detected_part = None

            if PRODUCT_13621 in barcode:
                detected_part = PRODUCT_13621
            elif PRODUCT_13622 in barcode:
                detected_part = PRODUCT_13622

            if detected_part:
                self.current_s1_barcode = barcode

                self.expected_part = detected_part
                self.expected_scanner = TARGET_SCANNER[detected_part]
                self.status = "WAIT_JUDGMENT"

                self.label_status.config(
                    text=f"SCAN DI {self.expected_scanner}",
                    bg=self.COLOR_STATUS_ACTIVE,
                    fg="#ffffff"
                )

                self.label_target.config(
                    text=f"Target Part : {detected_part}"
                )

                self.label_mode.config(
                    text="Mode : WAIT_JUDGMENT"
                )

                self.insert_table(
                    scanner,
                    barcode,
                    "SCAN_START",
                    "OK"
                )

                self.write_log(f"TARGET : {self.expected_scanner}")

                self.start_scan_2_timeout()

                target_pin = (
                    GPIO_13621
                    if detected_part == PRODUCT_13621
                    else GPIO_13622
                )

                start_relay_blink(target_pin)
                return

            else:
                self.cancel_scan_2_timeout()

                self.insert_table(
                    scanner,
                    barcode,
                    "SCAN_START",
                    "NG"
                )

                self.error_lock("ERROR ! PART INVALID")
                play_alarm_loop()
                return

        # =================================================
        # 4. JIKA S2 / S3 SCAN SAAT MENUNGGU S1
        # =================================================
        if self.status == "WAIT_START":
            self.insert_table(
                scanner,
                barcode,
                "SCAN_START",
                "NG"
            )

            self.error_lock("ERROR ! AWAL HARUS SCANNER 1")
            play_alarm_loop()
            return

        # =================================================
        # 5. JIKA S2 / S3 UNTUK JUDGMENT
        # =================================================
        elif self.status == "WAIT_JUDGMENT":
            self.cancel_scan_2_timeout()

            correct_scanner = scanner == self.expected_scanner
            correct_part = self.expected_part in barcode

            if correct_scanner and correct_part:
                stop_relay_blink()

                self.insert_table(
                    scanner,
                    barcode,
                    "SCAN_JUDGMENT",
                    "OK"
                )

                self.label_status.config(
                    text="VALIDASI BERHASIL",
                    bg=self.COLOR_STATUS_SUCCESS,
                    fg="#ffffff"
                )

                self.write_log("VALIDATION OK")

                # Sistem kembali ke WAIT_START,
                # tetapi current_s1_barcode tetap tersimpan.
                # Jadi barcode yang sama tidak bisa langsung discan ulang
                # sebelum ID Card reset discan.
                self.root.after(1500, self.reset_to_wait)

            else:
                stop_relay_blink()

                self.insert_table(
                    scanner,
                    barcode,
                    "SCAN_JUDGMENT",
                    "NG"
                )

                play_alarm_loop()

                if not correct_scanner:
                    self.error_lock(f"ERROR ! HARUS {self.expected_scanner}")
                elif not correct_part:
                    self.error_lock("ERROR ! KANBAN TIDAK SESUAI")

    # =====================================================
    # RESET WAIT
    # =====================================================
    def reset_to_wait(self):
        self.status = "WAIT_START"
        self.expected_part = ""
        self.expected_scanner = ""

        self.label_status.config(
            text="MENUNGGU SCAN AWAL",
            bg=self.COLOR_STATUS_WAIT,
            fg=self.COLOR_TEXT
        )

        self.label_target.config(
            text="Target Part : -"
        )

        self.label_mode.config(
            text="Mode : WAIT_START"
        )

        stop_relay_blink()

    # =====================================================
    # RESET UI
    # =====================================================
    def reset_ui(self):
        for item in self.table.get_children():
            self.table.delete(item)

        self.write_log("UI RESET")

    # =====================================================
    # EXPORT CSV
    # =====================================================
    def export_csv(self):
        rows = []

        for item in self.table.get_children():
            rows.append(self.table.item(item)["values"])

        with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)

            writer.writerow([
                "TIME",
                "SCANNER",
                "BARCODE",
                "PHASE",
                "RESULT"
            ])

            writer.writerows(rows)

        messagebox.showinfo(
            "SUCCESS",
            f"CSV SAVED:\n{CSV_FILE}"
        )

    # =====================================================
    # CLOSE
    # =====================================================
    def close_program(self):
        self.running = False

        self.cancel_scan_2_timeout()

        stop_all_sound()
        stop_timeout_alarm_sequence()
        stop_relay_blink()

        if GPIO:
            GPIO.cleanup()

        self.root.destroy()


# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    setup_audio()

    root = tk.Tk()
    app = PokaYokeSystem(root)

    root.mainloop()
