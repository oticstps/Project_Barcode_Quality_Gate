from pathlib import Path
import os
import sys
import time
import threading

import pygame

# Untuk Raspberry Pi
try:
    import RPi.GPIO as GPIO
except ModuleNotFoundError:
    GPIO = None
    print("PERINGATAN: Modul RPi.GPIO tidak ditemukan. GPIO hanya bisa berjalan di Raspberry Pi.")

# =========================
# DATA QR / BARCODE YANG VALID
# =========================
# QR referensi untuk scan urutan 2
QR_149 = "149^56K^136210C01000^SHIKAKE"
QR_88 = "88^57K^136220C01000^SHIKAKE"

# Barcode Balance sshaft 1 untuk scan urutan 1
BALANCE_SSHAFT_1 = [
    "48^56K^136210C01000^SHIKAKE",
    "135^56K^136210C01000^SHIKAKE",
    "29^48K^136210C01000^SHIKAKE",
    "62^48K^136210C01000^SHIKAKE",
]

# Barcode Balance sshaft 2 untuk scan urutan 1
BALANCE_SSHAFT_2 = [
    "9^49K^136220C01000^SHIKAKE",
    "4^49K^136220C01000^SHIKAKE",
    "128^57K^136220C01000^SHIKAKE",
    "39^57K^136220C01000^SHIKAKE",
]


def get_barcode_prefix(code):
    """
    Mengambil kode unik depan sebagai acuan judgment.

    Contoh:
    - 48^56K^136210C01000^SHIKAKE -> 48^56K^
    - 88^57K^136220C01000^SHIKAKE -> 88^57K^

    Bagian setelah tanda ^ kedua diabaikan.
    """
    if not code:
        return ""

    parts = code.strip().split("^")
    if len(parts) < 2:
        return code.strip()

    return f"{parts[0]}^{parts[1]}^"


QR_149_PREFIX = get_barcode_prefix(QR_149)
QR_88_PREFIX = get_barcode_prefix(QR_88)

BALANCE_SSHAFT_1_PREFIXES = {get_barcode_prefix(code) for code in BALANCE_SSHAFT_1}
BALANCE_SSHAFT_2_PREFIXES = {get_barcode_prefix(code) for code in BALANCE_SSHAFT_2}

# Kondisi OK:
# - Scan 1 Balance sshaft 1 -> Scan 2 harus QR_88
# - Scan 1 Balance sshaft 2 -> Scan 2 harus QR_149
SCAN1_PREFIX_TO_EXPECTED_SCAN2_PREFIX = {
    **{prefix: QR_88_PREFIX for prefix in BALANCE_SSHAFT_1_PREFIXES},
    **{prefix: QR_149_PREFIX for prefix in BALANCE_SSHAFT_2_PREFIXES},
}

VALID_SCAN1_PREFIXES = set(SCAN1_PREFIX_TO_EXPECTED_SCAN2_PREFIX.keys())

RESET_CODE = "OI-234066"
SCAN_2_TIMEOUT_SECONDS = 20

# =========================
# GPIO RELAY RASPBERRY PI 3 B+
# Mode BCM: GPIO 21 dan GPIO 20
# =========================
GPIO_149 = 21
GPIO_88 = 20

EXPECTED_SCAN2_PREFIX_TO_GPIO = {
    QR_149_PREFIX: GPIO_149,
    QR_88_PREFIX: GPIO_88,
}

# Modul relay Anda tampaknya aktif LOW:
# - GPIO LOW  = relay/lampu ON
# - GPIO HIGH = relay/lampu OFF
# Dengan setting ini, kondisi standby akan MATI.
RELAY_ON = None
RELAY_OFF = None
BLINK_INTERVAL_SECONDS = 0.5

# =========================
# PATH FILE SUARA
# Hanya alarm yang dipakai:
# - 0003.mp3 untuk NG / tidak sesuai
# - 0017.mp3 untuk timeout scan 2, lalu 0003.mp3
# =========================
BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "Media"

SOUND_ALARM = MEDIA_DIR / "0003.mp3"
SOUND_SCAN_2_TIMEOUT = MEDIA_DIR / "0017.mp3"

# =========================
# INIT AUDIO
# =========================
pygame.mixer.init()
pygame.mixer.set_num_channels(4)

ALARM_CHANNEL = pygame.mixer.Channel(0)
VOICE_CHANNEL = pygame.mixer.Channel(1)

timeout_alarm_stop_event = threading.Event()
timeout_alarm_thread = None

relay_blink_stop_event = threading.Event()
relay_blink_thread = None
active_relay_pin = None

# Jika True, program dikunci dan hanya RESET_CODE yang diterima.
# Dipakai saat timeout scan 2 atau scan 1 dan scan 2 tidak sesuai.
system_locked = False


# =========================
# SETUP GPIO
# =========================
def setup_gpio():
    global RELAY_ON, RELAY_OFF

    if GPIO is None:
        return

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    # Active LOW relay: HIGH = OFF, LOW = ON
    RELAY_ON = GPIO.LOW
    RELAY_OFF = GPIO.HIGH

    GPIO.setup(GPIO_149, GPIO.OUT, initial=RELAY_OFF)
    GPIO.setup(GPIO_88, GPIO.OUT, initial=RELAY_OFF)
    turn_off_all_relays()


def gpio_output(pin, state):
    if GPIO is None:
        print(f"GPIO SIMULASI: pin {pin} -> {state}")
        return

    GPIO.output(pin, state)


def turn_off_all_relays():
    if GPIO is None:
        return

    gpio_output(GPIO_149, RELAY_OFF)
    gpio_output(GPIO_88, RELAY_OFF)


# =========================
# RELAY BLINK
# =========================
def relay_blink_worker(pin):
    while not relay_blink_stop_event.is_set():
        gpio_output(pin, RELAY_ON)
        time.sleep(BLINK_INTERVAL_SECONDS)

        if relay_blink_stop_event.is_set():
            break

        gpio_output(pin, RELAY_OFF)
        time.sleep(BLINK_INTERVAL_SECONDS)

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
        relay_blink_thread.join(timeout=1)

    relay_blink_thread = None
    active_relay_pin = None
    turn_off_all_relays()


# =========================
# FUNGSI BANTU FILE SUARA
# =========================
def load_sound(file_path):
    if not file_path.exists():
        print(f"File suara tidak ditemukan: {file_path}")
        return None

    try:
        return pygame.mixer.Sound(str(file_path))
    except Exception as e:
        print(f"Gagal load suara: {file_path}")
        print("Error:", e)
        return None


def wait_sound_finish(channel, stop_event):
    while channel.get_busy():
        if stop_event.is_set():
            channel.stop()
            break
        time.sleep(0.05)


# =========================
# FUNGSI SUARA
# Semua suara normal dihapus.
# Suara hanya aktif untuk:
# 1. Timeout scan 2
# 2. Scan 1 dan scan 2 tidak sesuai
# =========================
def stop_all_sound():
    pygame.mixer.stop()


def play_alarm_loop():
    sound = load_sound(SOUND_ALARM)
    if sound is None:
        return

    ALARM_CHANNEL.stop()
    ALARM_CHANNEL.play(sound, loops=-1)


# =========================
# ALARM TIMEOUT BERGANTIAN
# =========================
def timeout_alarm_worker():
    """
    Jika scan 2 timeout:
    0017.mp3 diputar lebih dulu,
    lalu 0003.mp3,
    kemudian bergantian terus sampai reset discan.
    """
    timeout_sound = load_sound(SOUND_SCAN_2_TIMEOUT)
    alarm_sound = load_sound(SOUND_ALARM)

    if timeout_sound is None or alarm_sound is None:
        return

    while not timeout_alarm_stop_event.is_set():
        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()
        VOICE_CHANNEL.play(timeout_sound)
        wait_sound_finish(VOICE_CHANNEL, timeout_alarm_stop_event)

        if timeout_alarm_stop_event.is_set():
            break

        time.sleep(0.2)

        ALARM_CHANNEL.stop()
        VOICE_CHANNEL.stop()
        ALARM_CHANNEL.play(alarm_sound)
        wait_sound_finish(ALARM_CHANNEL, timeout_alarm_stop_event)

        if timeout_alarm_stop_event.is_set():
            break

        time.sleep(0.2)


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
    ALARM_CHANNEL.stop()
    VOICE_CHANNEL.stop()

    if timeout_alarm_thread is not None and timeout_alarm_thread.is_alive():
        timeout_alarm_thread.join(timeout=1)

    timeout_alarm_thread = None


def is_timeout_alarm_active():
    return timeout_alarm_thread is not None and timeout_alarm_thread.is_alive()


def is_any_alarm_active():
    return is_timeout_alarm_active() or ALARM_CHANNEL.get_busy()


# =========================
# INPUT DENGAN TIMEOUT
# =========================
def input_with_timeout(prompt, timeout_seconds):
    """
    Membaca input scanner dengan batas waktu.

    Raspberry Pi / Linux memakai select.
    Windows fallback memakai msvcrt.

    Return:
    - string kode jika scanner/user menekan Enter sebelum timeout
    - None jika timeout
    """
    print(prompt, end="", flush=True)

    # Fallback Windows
    if os.name == "nt":
        import msvcrt

        buffer = ""
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed >= timeout_seconds:
                print()
                return None

            if msvcrt.kbhit():
                char = msvcrt.getwch()

                if char in ("\r", "\n"):
                    print()
                    return buffer.strip()

                if char == "\b":
                    if buffer:
                        buffer = buffer[:-1]
                        print("\b \b", end="", flush=True)
                else:
                    buffer += char
                    print(char, end="", flush=True)

            time.sleep(0.01)

    # Raspberry Pi / Linux
    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)

    if ready:
        return sys.stdin.readline().strip()

    print()
    return None


# =========================
# HANDLE RESET
# =========================
def handle_reset():
    global system_locked

    system_locked = False
    stop_timeout_alarm_sequence()
    stop_all_sound()
    stop_relay_blink()

    print("RESET OK - Semua alarm dan relay dimatikan.")
    print("-------------------------------------")


# =========================
# PROGRAM UTAMA
# =========================
def main():
    global system_locked

    setup_gpio()

    print("=== PROGRAM SCAN QR + GPIO RELAY ===")
    print("Program mulai dijalankan.")
    print("Scan urutan 1 dan urutan 2.")
    print("Scan reset untuk mematikan alarm:", RESET_CODE)
    print("Timeout scan 2:", SCAN_2_TIMEOUT_SECONDS, "detik")
    print("GPIO 21 aktif berkedip jika scan 2 yang diharapkan:", QR_149_PREFIX)
    print("GPIO 20 aktif berkedip jika scan 2 yang diharapkan:", QR_88_PREFIX)
    print("Acuan scan 1 Balance sshaft 1:", sorted(BALANCE_SSHAFT_1_PREFIXES))
    print("Acuan scan 1 Balance sshaft 2:", sorted(BALANCE_SSHAFT_2_PREFIXES))
    print("Standby relay/lampu: OFF")
    print("Semua suara normal dimatikan.")
    print("Suara hanya aktif untuk timeout dan hasil NG / tidak sesuai.")
    print("-------------------------------------")
    print("Folder Media         :", MEDIA_DIR)
    print("Suara ALARM          :", SOUND_ALARM)
    print("Suara TIMEOUT SCAN 2 :", SOUND_SCAN_2_TIMEOUT)
    print("-------------------------------------")

    while True:
        # =========================
        # SCAN URUTAN 1
        # =========================
        scan_1 = input("Scan QR/Barcode urutan 1: ").strip()

        # Reset selalu boleh dilakukan
        if scan_1 == RESET_CODE:
            handle_reset()
            continue

        # Jika sistem terkunci karena timeout / NG, hanya reset yang diterima
        if system_locked or is_any_alarm_active():
            print("Sistem terkunci karena timeout / NG. Scan reset terlebih dahulu.")
            print("Input selain reset diabaikan.")
            print("-------------------------------------")
            continue

        scan_1_prefix = get_barcode_prefix(scan_1)

        # Scan 1 tidak sesuai dengan daftar kondisi OK
        if scan_1_prefix not in VALID_SCAN1_PREFIXES:
            system_locked = True
            print("Scan urutan 1 tidak sesuai / tidak terdaftar.")
            print("HASIL: NG / SCAN 1 TIDAK SESUAI")
            print("ALARM AKTIF")
            print("Scan reset untuk mematikan alarm.")
            play_alarm_loop()
            print("-------------------------------------")
            continue

        expected_scan_2_prefix = SCAN1_PREFIX_TO_EXPECTED_SCAN2_PREFIX[scan_1_prefix]

        print("Scan urutan 1 diterima:", scan_1)
        print("Acuan scan 1:", scan_1_prefix)
        print("Scan urutan 2 yang harus sesuai:", expected_scan_2_prefix)

        relay_pin = EXPECTED_SCAN2_PREFIX_TO_GPIO[expected_scan_2_prefix]
        print(f"GPIO {relay_pin} ON dan berkedip sampai scan 2 sesuai.")
        start_relay_blink(relay_pin)

        print("Silakan scan urutan 2 maksimal", SCAN_2_TIMEOUT_SECONDS, "detik.")

        # =========================
        # SCAN URUTAN 2 DENGAN TIMEOUT
        # =========================
        scan_2 = input_with_timeout(
            "Scan QR/Barcode urutan 2: ",
            SCAN_2_TIMEOUT_SECONDS,
        )

        # Jika scan 2 timeout
        if scan_2 is None:
            system_locked = True
            print("TIMEOUT - Scan urutan 2 tidak diterima dalam 20 detik.")
            print("HASIL: NG / TIMEOUT")
            print("ALARM TIMEOUT AKTIF")
            print("Relay tetap berkedip sampai reset discan.")
            start_timeout_alarm_sequence()
            print("-------------------------------------")
            continue

        # Reset saat posisi scan 2
        if scan_2 == RESET_CODE:
            handle_reset()
            continue

        scan_2_prefix = get_barcode_prefix(scan_2)

        print("Scan urutan 2 diterima:", scan_2)
        print("Acuan scan 2:", scan_2_prefix)

        # =========================
        # PENGECEKAN HASIL
        # =========================
        if scan_2_prefix == expected_scan_2_prefix:
            system_locked = False
            print("HASIL: OK")
            print(f"GPIO {relay_pin} OFF karena scan 2 sesuai.")
            stop_relay_blink()
        else:
            system_locked = True
            print("HASIL: NG / TIDAK SESUAI")
            print("ALARM AKTIF")
            print("Relay tetap berkedip sampai reset discan.")
            play_alarm_loop()

        print("-------------------------------------")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop_timeout_alarm_sequence()
        stop_all_sound()
        stop_relay_blink()

        if GPIO is not None:
            GPIO.cleanup()

        pygame.mixer.quit()
        print("\nProgram dihentikan.")
