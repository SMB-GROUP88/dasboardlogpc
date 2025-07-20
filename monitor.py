import time, datetime, socket, requests, mss, io, os, json, threading, sys
from PIL import Image
import pygetwindow as gw
import pystray
from pystray import MenuItem as item
from PIL import Image as PILImage
import tkinter as tk
from tkinter import simpledialog
import requests

SERVER_URL = "http://192.168.29.231:5000/logs"
APP_NAME = "PCMonitor"
CONFIG_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~/.config"), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
ICON_FILE = "togelup.png"  # ganti dengan path ikon PNG kamu jika punya

monitoring = False
monitor_thread = None

def get_active_window():
    try:
        window = gw.getActiveWindow()
        return window.title if window else None
    except Exception as e:
        print(f"[ERROR] get_active_window: {e}")
        return None

def get_pc_name():
    return socket.gethostname()

def take_screenshot():
    with mss.mss() as sct:
        screenshot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", (screenshot.width, screenshot.height), screenshot.rgb)
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr

def load_user_name():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f).get("name")
    else:
        root = tk.Tk()
        root.withdraw()
        name = simpledialog.askstring("Nama Pengguna", "Masukkan nama pengguna:")
        if not name:
            print("[INFO] Nama tidak dimasukkan. Aplikasi keluar.")
            sys.exit(0)
        with open(CONFIG_FILE, "w") as f:
            json.dump({"name": name}, f)
        return name

def send_log_to_server(name, pc_name, active_window, timestamp, screenshot):
    try:
        files = {
            "screenshot": ("screenshot.png", screenshot, "image/png")
        }
        data = {
            "name": name,
            "pc_name": pc_name,
            "active_window": active_window,
            "timestamp": timestamp
        }
        response = requests.post(SERVER_URL, files=files, data=data)
        if response.status_code != 200:
            print(f"[ERROR] Gagal kirim ke server: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"[ERROR] Gagal kirim log: {e}")

def monitor_loop():
    global monitoring
    name = load_user_name()
    pc_name = get_pc_name()
    last_window = None
    print(f"[MONITORING] Dimulai untuk: {name} di {pc_name}")
    while monitoring:
        active_window = get_active_window()
        if active_window and active_window != last_window:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            screenshot = take_screenshot()
            send_log_to_server(name, pc_name, active_window, timestamp, screenshot)
            last_window = active_window
        time.sleep(3)

def start_monitoring(icon=None, item=None):
    global monitoring, monitor_thread
    if not monitoring:
        monitoring = True
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        print("[INFO] Monitoring dimulai.")

def stop_monitoring(icon=None, item=None):
    global monitoring
    monitoring = False
    print("[INFO] Monitoring dihentikan.")

def exit_app(icon=None, item=None):
    stop_monitoring()
    icon.stop()
    print("[INFO] Aplikasi dihentikan.")

def create_image():
    if os.path.exists(ICON_FILE):
        try:
            return PILImage.open(ICON_FILE)
        except Exception as e:
            print(f"[WARNING] Gagal load togelup.png, fallback ke ikon biru. Error: {e}")
    
    # fallback icon
    image = PILImage.new('RGB', (64, 64), "blue")
    return image

def run_tray_app():
    menu = (
        item('Start Monitoring', start_monitoring),
        item('Stop Monitoring', stop_monitoring),
        item('Exit', exit_app)
    )
    icon = pystray.Icon("monitor_app", create_image(), "PC Monitor", menu)
    icon.run()

if __name__ == "__main__":
    run_tray_app()
