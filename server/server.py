from flask import Flask, request, jsonify, render_template, abort
from datetime import datetime, timedelta
from supabase import create_client
from werkzeug.utils import secure_filename
import os
from dotenv import load_dotenv
from collections import defaultdict
import traceback
import threading
import time

load_dotenv()

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def auto_delete_old_logs():
    while True:
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=3)
            cutoff_str = cutoff_date.isoformat()

            response = supabase.table("logs").delete().lt("timestamp", cutoff_str).execute()
            print(f"[AUTO DELETE] Log sebelum {cutoff_str} berhasil dihapus.")
        except Exception as e:
            print(f"[AUTO DELETE ERROR] {e}")
        
        time.sleep(24 * 60 * 60)  # setiap 24 jam


@app.route("/logs", methods=["POST"])
def receive_log():
    try:
        print("Received a POST request to /logs")

        name = request.form.get("name")
        pc_name = request.form.get("pc_name")
        window = request.form.get("active_window")
        timestamp = request.form.get("timestamp")
        screenshot = request.files.get("screenshot")

        print(f"Received data: name={name}, pc_name={pc_name}, window={window}, timestamp={timestamp}")

        if not screenshot:
            return "No screenshot received", 400

        screenshot_bytes = screenshot.read()
        filename = secure_filename(f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
        file_path = f"screenshots/{filename}"

        print(f"Uploading file to Supabase Storage at path: {file_path}")

        try:
            upload_response = supabase.storage.from_("screenshots").upload(file_path, screenshot_bytes)
        except Exception as e:
            print(f"Exception during upload: {e}")
            return jsonify({"error": f"Upload failed: {str(e)}"}), 500

        public_url_response = supabase.storage.from_("screenshots").get_public_url(file_path)

        public_url = None
        if isinstance(public_url_response, dict):
            public_url = public_url_response.get("publicUrl")
        elif hasattr(public_url_response, "data") and isinstance(public_url_response.data, dict):
            public_url = public_url_response.data.get("publicUrl")
        else:
            public_url = str(public_url_response)

        print(f"Public URL: {public_url}")

        insert_response = supabase.table("logs").insert({
            "username": name,
            "pc_name": pc_name,
            "active_window": window,
            "timestamp": timestamp,
            "image_url": public_url
        }).execute()

        if not hasattr(insert_response, "data") or insert_response.data is None:
            return jsonify({"error": "Insert failed, no data returned"}), 500

        return "OK", 200

    except Exception as e:
        print(f"Error occurred: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/logs")
def logs_dashboard():
    search = request.args.get("search", "").lower()  # Mendapatkan parameter pencarian
    date_filter = request.args.get("date")  # Mendapatkan parameter tanggal (YYYY-MM-DD)
    
    # Mengambil log dari database Supabase
    result = supabase.table("logs").select("*").order("timestamp", desc=True).execute()
    error = getattr(result, "error", None)
    data = getattr(result, "data", [])

    if error:
        return jsonify({"error": str(error)}), 500
    if not data:
        return jsonify({"error": "No data found"}), 500

    # Filter berdasarkan pencarian
    if search:
        data = [log for log in data if search in (log.get("username") or "").lower()]

    # Filter berdasarkan tanggal, jika ada
    if date_filter:
        try:
            date_obj = datetime.strptime(date_filter, "%Y-%m-%d")
            data = [log for log in data if datetime.fromisoformat(log["timestamp"]).date() == date_obj.date()]
        except ValueError:
            return jsonify({"error": "Invalid date format, expected YYYY-MM-DD"}), 400

    # Ringkasan log per pengguna
    summary = defaultdict(lambda: {"log_count": 0, "last_active": None, "status": "OFF"})

    # Tentukan waktu aktif maksimal 5 menit yang lalu
    max_inactive_duration = timedelta(minutes=5)

    for log in data:
        key = (log["username"], log["pc_name"])
        summary[key]["log_count"] += 1
        current_ts = datetime.fromisoformat(log["timestamp"])
        if summary[key]["last_active"] is None or current_ts > summary[key]["last_active"]:
            summary[key]["last_active"] = current_ts

    # Tentukan status aktif (ON/OFF)
    for (username, pc_name), info in summary.items():
        last_active = info["last_active"]
        if last_active and (datetime.utcnow() - last_active) <= max_inactive_duration:
            info["status"] = "ON"  # Pengguna aktif
        else:
            info["status"] = "OFF"  # Pengguna tidak aktif

    # Menyiapkan data pengguna untuk ditampilkan
    users = []
    for (username, pc_name), info in summary.items():
        users.append({
            "username": username,
            "pc_name": pc_name,
            "log_count": info["log_count"],
            "last_active": info["last_active"].strftime("%Y-%m-%d %H:%M:%S"),
            "status": info["status"],  # Status aktif
        })

    return render_template("logs.html", users=users, search=search, date_filter=date_filter)





@app.route("/user/<username>")
def user_logs(username):
    username_lower = username.lower()
    try:
        result = supabase.table("logs").select("*").ilike("username", username_lower).order("timestamp", desc=True).execute()
        if getattr(result, "error", None):
            return f"Error: {result.error}", 500
        logs = getattr(result, "data", [])
        if logs is None:
            logs = []
        return render_template("user_logs.html", username=username, logs=logs)
    except Exception as e:
        print("Error occurred:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# Auto-delete thread selalu jalan, baik di gunicorn atau dev
threading.Thread(target=auto_delete_old_logs, daemon=True).start()

if __name__ == "__main__":
    # Jalankan Flask hanya kalau di mode dev (tanpa gunicorn)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

