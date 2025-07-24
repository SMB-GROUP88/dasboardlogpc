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
from dateutil import parser
from datetime import timezone

load_dotenv()
app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_whitelist_ips():
    res = supabase.table("ip_whitelist").select("ip").execute()
    return [row["ip"] for row in (res.data or [])]

def add_whitelist_ip(ip):
    return supabase.table("ip_whitelist").insert({"ip": ip}).execute()

def remove_whitelist_ip(ip):
    return supabase.table("ip_whitelist").delete().eq("ip", ip).execute()

def auto_delete_old_logs():
    while True:
        try:
            # Buat cutoff_datetime dengan timezone UTC agar bisa dibandingkan dengan timestamp offset-aware
            cutoff_datetime = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(days=3)
            print(f"[AUTO DELETE] Menghapus log dengan timestamp < {cutoff_datetime}")

            result = supabase.table("logs").select("id, timestamp").execute()
            logs = result.data or []

            logs_to_delete = []
            for log in logs:
                try:
                    raw_ts = log.get("timestamp")
                    if not raw_ts:
                        continue  # skip jika timestamp kosong/null
                    ts = parser.parse(raw_ts)
                    # ts sudah offset-aware, cutoff_datetime juga sudah offset-aware
                    if ts < cutoff_datetime:
                        print(f"[DELETE] Log ID {log['id']} dengan timestamp {ts} masuk daftar hapus")
                        logs_to_delete.append(log["id"])
                except Exception as e:
                    print(f"[SKIP] Bad timestamp format: {log}, error: {e}")

            print(f"[AUTO DELETE] Total log dihapus: {len(logs_to_delete)}")

            for log_id in logs_to_delete:
                supabase.table("logs").delete().eq("id", log_id).execute()

        except Exception as e:
            print(f"[AUTO DELETE ERROR] {e}")

        # Jalankan setiap hari
        time.sleep(60 * 60 * 24)

@app.before_request
def block_unallowed():
    if request.endpoint == "logs_dashboard":
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
        allowed_ips = get_whitelist_ips()
        print(f"Client IP: {client_ip}, Allowed IPs: {allowed_ips}")
        if client_ip not in allowed_ips:
            abort(403, description="Access denied: your IP is not allowed.")

@app.route("/admin/whitelist", methods=["GET", "POST"])
def manage_whitelist():
    if request.method == "POST":
        ip = request.form.get("ip")
        action = request.form.get("action")
        if action == "add" and ip:
            add_whitelist_ip(ip)
        elif action == "remove" and ip:
            remove_whitelist_ip(ip)
    ips = get_whitelist_ips()
    return render_template("manage_whitelist.html", ips=ips)

@app.route("/logs", methods=["POST"])
def receive_log():
    try:
        print("Received a POST request to /logs")

        name = request.form.get("name")
        pc_name = request.form.get("pc_name")
        window = request.form.get("active_window")
        timestamp = request.form.get("timestamp")  # Harus UTC ISO 8601 dari klien
        screenshot = request.files.get("screenshot")

        if not screenshot:
            return "No screenshot received", 400

        screenshot_bytes = screenshot.read()
        filename = secure_filename(f"{name}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.png")
        file_path = f"screenshots/{filename}"

        supabase.storage.from_("screenshots").upload(file_path, screenshot_bytes)

        public_url_response = supabase.storage.from_("screenshots").get_public_url(file_path)
        public_url = getattr(public_url_response, "data", {}).get("publicUrl")
        if not public_url:
            public_url = str(public_url_response)  # fallback jika tidak dapat

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
    search = request.args.get("search", "").lower()
    date_filter = request.args.get("date")

    result = supabase.table("logs").select("*").order("timestamp", desc=True).execute()
    error = getattr(result, "error", None)
    data = getattr(result, "data", [])

    if error:
        return jsonify({"error": str(error)}), 500
    if not data:
        return render_template("logs.html", users=[], search=search, date_filter=date_filter)

    if search:
        data = [log for log in data if search in (log.get("username") or "").lower()]

    if date_filter:
        try:
            date_obj = datetime.strptime(date_filter, "%Y-%m-%d").date()
            filtered_data = []
            for log in data:
                try:
                    raw_ts = log.get("timestamp")
                    if not raw_ts:
                        continue
                    ts = parser.parse(raw_ts).date()
                    if ts == date_obj:
                        filtered_data.append(log)
                except Exception as e:
                    print(f"Timestamp parsing error: {e}")
            data = filtered_data
        except ValueError:
            return jsonify({"error": "Invalid date format, expected YYYY-MM-DD"}), 400

    summary = defaultdict(lambda: {"log_count": 0, "last_active": None, "status": "OFF"})
    max_inactive_duration = timedelta(minutes=5)

    for log in data:
        key = (log["username"], log["pc_name"])
        summary[key]["log_count"] += 1
        try:
            raw_ts = log.get("timestamp")
            if not raw_ts:
                continue
            current_ts = parser.parse(raw_ts)
        except Exception as e:
            print(f"Error parsing timestamp: {e}")
            continue

        if summary[key]["last_active"] is None or current_ts > summary[key]["last_active"]:
            summary[key]["last_active"] = current_ts

    for (username, pc_name), info in summary.items():
        last_active = info["last_active"]
        if last_active and (datetime.utcnow() - last_active) <= max_inactive_duration:
            info["status"] = "ON"
        else:
            info["status"] = "OFF"

    users = []
    for (username, pc_name), info in summary.items():
        users.append({
            "username": username,
            "pc_name": pc_name,
            "log_count": info["log_count"],
            "last_active": info["last_active"].strftime("%Y-%m-%d %H:%M:%S") if info["last_active"] else "-",
            "status": info["status"],
        })

    return render_template("logs.html", users=users, search=search, date_filter=date_filter)

@app.route("/user/<username>")
def user_logs(username):
    username_lower = username.lower()
    try:
        result = supabase.table("logs").select("*").ilike("username", username_lower).order("timestamp", desc=True).execute()
        if getattr(result, "error", None):
            return f"Error: {result.error}", 500
        logs = getattr(result, "data", []) or []
        return render_template("user_logs.html", username=username, logs=logs)
    except Exception as e:
        print("Error occurred:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# Jalankan thread auto-delete log
threading.Thread(target=auto_delete_old_logs, daemon=True).start()
print("[AUTO DELETE THREAD] Started background auto-delete task...")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
