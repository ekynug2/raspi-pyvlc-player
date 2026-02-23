#!/usr/bin/env python3
"""
Raspberry Pi Video Signage Player
Web-based control panel with python-vlc backend.
"""

import os
import json
import time
import shutil
import threading
import signal
import sys
import subprocess
from pathlib import Path
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

import vlc
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
VIDEO_DIR = BASE_DIR / "videos"
PLAYLIST_FILE = BASE_DIR / "playlist.json"
ALLOWED_EXTENSIONS = {"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "ts", "m4v"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB upload limit
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

USERNAME = os.environ.get("DASHBOARD_USER", "admin")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")

# Hash the password if it's stored as plaintext (not already hashed)
# Werkzeug hashes start with method prefix like "scrypt:", "pbkdf2:", etc.
_KNOWN_HASH_PREFIXES = ("scrypt:", "pbkdf2:", "sha256:", "sha512:")
if PASSWORD.startswith(_KNOWN_HASH_PREFIXES):
    PASSWORD_HASH = PASSWORD
else:
    PASSWORD_HASH = generate_password_hash(PASSWORD)

# ---------------------------------------------------------------------------
# Rate limiting for login
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 300  # 5 minutes


def _is_rate_limited(ip: str) -> bool:
    """Check if an IP has exceeded login attempt limits."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Remove old attempts outside the window
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SEC]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS


def _record_attempt(ip: str):
    """Record a failed login attempt."""
    _login_attempts.setdefault(ip, []).append(time.time())

@app.before_request
def csrf_protect():
    if request.method == "POST" and request.endpoint != "api_login":
        token = session.get("csrf_token")
        request_token = request.form.get("csrf_token")
        if not request_token:
            request_token = request.headers.get("X-CSRF-Token")
        if not token or token != request_token:
            error_msg = "Invalid CSRF token"
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": error_msg}), 403
            return error_msg, 403

@app.context_processor
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return dict(csrf_token=session["csrf_token"])

# ---------------------------------------------------------------------------
# VLC Player Wrapper
# ---------------------------------------------------------------------------

class SignagePlayer:
    """Wraps python-vlc to manage a looping playlist for digital signage."""

    def __init__(self, video_dir: Path, playlist_file: Path):
        self.video_dir = video_dir
        self.playlist_file = playlist_file
        self.video_dir.mkdir(parents=True, exist_ok=True)

        # VLC instance optimized for Raspberry Pi Trixie (Wayland)
        self.instance = vlc.Instance(
            "--fullscreen",
            "--no-video-title-show",
            "--no-osd",
            "--aout=alsa",
            "--mouse-hide-timeout=0",
            "--video-on-top",
            "--no-video-deco",
            "--aspect-ratio=16:9",
            "--no-embedded-video" if os.environ.get("WAYLAND_DISPLAY") else "" 
        )
        self.player = self.instance.media_player_new()
        self.player.video_set_scale(0)

        # Playlist state
        self.playlist: list[dict] = []   # ordered list of dicts
        self.current_index: int = 0
        self.is_looping: bool = True
        self.auto_play: bool = True     # auto-play on boot for signage

        self._lock = threading.Lock()
        
        # State control
        self._is_playing_state = False
        self._next_event = threading.Event()

        # Load saved playlist
        self._load_playlist()

        # Attach end-reached event so we can advance to the next video
        events = self.player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end_reached)

        # Start background threads
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()
        
        self._scheduler_thread = threading.Thread(target=self._schedule_checker, daemon=True)
        self._scheduler_thread.start()

        # Auto-play on startup if enabled and playlist is not empty
        if self.auto_play and self.playlist:
            def _delayed_autoplay():
                time.sleep(2)  # wait for VLC to fully initialize
                self.play()
                print(f"Auto-play started")
            threading.Thread(target=_delayed_autoplay, daemon=True).start()

    # -- Playlist persistence ------------------------------------------------

    def _load_playlist(self):
        """Load playlist from JSON file, falling back to directory scan."""
        if self.playlist_file.exists():
            try:
                data = json.loads(self.playlist_file.read_text())
                raw_playlist = data.get("playlist", [])
                parsed = []
                for item in raw_playlist:
                    if isinstance(item, str):
                        parsed.append({"filename": item, "active": True, "schedule": {}})
                    elif isinstance(item, dict) and "filename" in item:
                        parsed.append(item)
                
                self.playlist = [p for p in parsed if (self.video_dir / p["filename"]).is_file()]
                self.is_looping = data.get("loop", True)
                self.auto_play = data.get("auto_play", True)
            except (json.JSONDecodeError, KeyError):
                self.playlist = []
        if not self.playlist:
            self.playlist = self._scan_videos()
        self._save_playlist()

    def _save_playlist(self):
        """Persist current playlist order to disk."""
        data = {
            "playlist": self.playlist,
            "loop": self.is_looping,
            "auto_play": self.auto_play,
        }
        self.playlist_file.write_text(json.dumps(data, indent=2))

    def _scan_videos(self) -> list[dict]:
        """Return sorted list of video dictionary configs in the video directory."""
        files = []
        for f in sorted(self.video_dir.iterdir()):
            if f.suffix.lstrip(".").lower() in ALLOWED_EXTENSIONS:
                files.append({"filename": f.name, "active": True, "schedule": {}})
        return files

    # -- VLC event handlers & Playback loop ----------------------------------

    def _on_end_reached(self, event):
        """Called when a single media finishes playing."""
        self._next_event.set()

    def _is_valid(self, item: dict) -> bool:
        if not item.get("active", True):
            return False
            
        sched = item.get("schedule", {})
        if not sched:
            return True
            
        from datetime import datetime
        now = datetime.now()
        
        start_date_str = sched.get("start_date")
        end_date_str = sched.get("end_date")
        start_time_str = sched.get("start_time")
        end_time_str = sched.get("end_time")

        if start_date_str:
            try:
                if now.date() < datetime.strptime(start_date_str, "%Y-%m-%d").date():
                    return False
            except ValueError: pass
        if end_date_str:
            try:
                if now.date() > datetime.strptime(end_date_str, "%Y-%m-%d").date():
                    return False
            except ValueError: pass

        if start_time_str:
            try:
                if now.time() < datetime.strptime(start_time_str, "%H:%M").time():
                    return False
            except ValueError: pass
        if end_time_str:
            try:
                if now.time() > datetime.strptime(end_time_str, "%H:%M").time():
                    return False
            except ValueError: pass
            
        return True

    def _playback_loop(self):
        while True:
            self._next_event.wait()
            self._next_event.clear()
            
            with self._lock:
                if not self._is_playing_state:
                    continue
                if not self.playlist:
                    self._is_playing_state = False
                    continue
                
                # Advance to next valid video
                found = False
                for _ in range(len(self.playlist)):
                    self.current_index = (self.current_index + 1) % len(self.playlist)
                    if self._is_valid(self.playlist[self.current_index]):
                        found = True
                        break
                
                if not found:
                    self._is_playing_state = False
                    self.player.stop()
                else:
                    self._play_current()

    def _schedule_checker(self):
        while True:
            time.sleep(5)
            with self._lock:
                if self._is_playing_state and self.playlist and 0 <= self.current_index < len(self.playlist):
                    curr_item = self.playlist[self.current_index]
                    if not self._is_valid(curr_item):
                        print(f"Skipping video due to schedule check: {curr_item['filename']}")
                        self.player.stop()
                        self._next_event.set()

    def _play_current(self):
        if not self.playlist or self.current_index >= len(self.playlist):
            return
        
        item = self.playlist[self.current_index]
        path = str(self.video_dir / item["filename"])
        media = self.instance.media_new(path)
        self.player.set_media(media)
        self.player.play()
        self._is_playing_state = True
        
        def force_fullscreen():
            time.sleep(0.5)
            self.player.video_set_aspect_ratio("16:9")
            self.player.set_fullscreen(True)
        threading.Thread(target=force_fullscreen, daemon=True).start()

    # -- Transport controls --------------------------------------------------

    def play(self, index: int | None = None):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
                
            if index is not None:
                if 0 <= index < len(self.playlist):
                    self.current_index = index
                    self._play_current()
                    self._is_playing_state = True
                else:
                    return {"error": "Index out of range"}
            else:
                state = self.player.get_state()
                if state in (vlc.State.Paused,):
                    self.player.play()
                    self._is_playing_state = True
                else:
                    if not self._is_valid(self.playlist[self.current_index]):
                        self._is_playing_state = True
                        self._next_event.set()
                    else:
                        self._play_current()
                        self._is_playing_state = True
            
            return {"status": "playing"}

    def pause(self):
        self.player.pause()
        self._is_playing_state = False
        return {"status": "paused"}

    def stop(self):
        self.player.stop()
        self._is_playing_state = False
        return {"status": "stopped"}

    def next(self):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
            self._is_playing_state = True
            self.player.stop()
            self._next_event.set()
            return {"status": "next"}

    def previous(self):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
            self.player.stop()
            # find previous valid
            found = False
            for _ in range(len(self.playlist)):
                self.current_index = (self.current_index - 1) % len(self.playlist)
                if self._is_valid(self.playlist[self.current_index]):
                    found = True
                    break
            if found:
                self._play_current()
            else:
                self._is_playing_state = False
            return {"status": "previous"}

    def set_volume(self, vol: int):
        vol = max(0, min(150, vol))
        self.player.audio_set_volume(vol)
        return {"volume": vol}

    def seek(self, position: float):
        position = max(0.0, min(1.0, position))
        self.player.set_position(position)
        return {"position": position}

    def set_loop(self, loop: bool):
        self.is_looping = loop
        self._save_playlist()
        return {"loop": loop}

    def set_auto_play(self, auto_play: bool):
        self.auto_play = auto_play
        self._save_playlist()
        return {"auto_play": auto_play}

    def update_item(self, filename: str, active: bool, schedule: dict):
        with self._lock:
            for item in self.playlist:
                if item["filename"] == filename:
                    if active is not None:
                        item["active"] = bool(active)
                    if schedule is not None:
                        item["schedule"] = schedule
                    self._save_playlist()
                    break
        return self.playlist

    # -- Status --------------------------------------------------------------

    def status(self) -> dict:
        state = str(self.player.get_state()).split(".")[-1]
        length = self.player.get_length()      # ms
        pos = self.player.get_position()        # 0.0-1.0
        current_time = self.player.get_time()   # ms
        volume = self.player.audio_get_volume()

        current_file = ""
        if self.playlist and 0 <= self.current_index < len(self.playlist):
            current_file = self.playlist[self.current_index]["filename"]

        return {
            "state": state,
            "current_file": current_file,
            "current_index": self.current_index,
            "length": length,
            "time": current_time,
            "position": pos if pos >= 0 else 0,
            "volume": volume if volume >= 0 else 0,
            "loop": self.is_looping,
            "auto_play": self.auto_play,
            "playlist_count": len(self.playlist),
        }

    # -- Playlist management -------------------------------------------------

    def get_playlist(self) -> list[dict]:
        return list(self.playlist)

    def add_video(self, filename: str):
        with self._lock:
            if not any(p["filename"] == filename for p in self.playlist):
                self.playlist.append({"filename": filename, "active": True, "schedule": {}})
                self._save_playlist()
        return {"playlist": self.playlist}

    def remove_video(self, filename: str):
        with self._lock:
            self.playlist = [p for p in self.playlist if p["filename"] != filename]
            if self.current_index >= len(self.playlist) and self.playlist:
                self.current_index = 0
            self._save_playlist()
        return {"playlist": self.playlist}

    def reorder_playlist(self, new_order: list):
        with self._lock:
            valid = []
            for item in new_order:
                if isinstance(item, str) and (self.video_dir / item).is_file():
                    valid.append({"filename": item, "active": True, "schedule": {}})
                elif isinstance(item, dict) and "filename" in item and (self.video_dir / item["filename"]).is_file():
                    valid.append(item)
            self.playlist = valid
            self.current_index = 0
            self._save_playlist()
        return {"playlist": self.playlist}

    def delete_video_file(self, filename: str):
        self.remove_video(filename)
        filepath = self.video_dir / filename
        if filepath.is_file():
            filepath.unlink()
        return {"deleted": filename}

    def release(self):
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
            print("VLC resources released.")
        except Exception as e:
            print(f"Error releasing VLC: {e}")

# ---------------------------------------------------------------------------
# Initialise player
# ---------------------------------------------------------------------------
player = SignagePlayer(VIDEO_DIR, PLAYLIST_FILE)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "logged_in" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        client_ip = request.remote_addr or "unknown"
        if _is_rate_limited(client_ip):
            return render_template("login.html", error="Too many attempts. Try again in 5 minutes.")
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == USERNAME and check_password_hash(PASSWORD_HASH, password):
            session["logged_in"] = True
            session["user"] = username
            # Clear failed attempts on success
            _login_attempts.pop(client_ip, None)
            return redirect(url_for("index"))
        _record_attempt(client_ip)
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    client_ip = request.remote_addr or "unknown"
    if _is_rate_limited(client_ip):
        return jsonify({"error": "Too many attempts. Try again in 5 minutes."}), 429
    username = data.get("username", "")
    password = data.get("password", "")
    if username == USERNAME and check_password_hash(PASSWORD_HASH, password):
        session["logged_in"] = True
        session["user"] = username
        _login_attempts.pop(client_ip, None)
        return jsonify({"status": "ok"})
    _record_attempt(client_ip)
    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.route("/api/check-auth")
def api_check_auth():
    return jsonify({"logged_in": session.get("logged_in", False)})


# ---------------------------------------------------------------------------
# Flask Routes  --  Pages
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Flask Routes  --  REST API
# ---------------------------------------------------------------------------

# -- Transport --

@app.route("/api/play", methods=["POST"])
@login_required
def api_play():
    data = request.get_json(silent=True) or {}
    index = data.get("index")
    if index is not None:
        index = int(index)
    return jsonify(player.play(index))


@app.route("/api/pause", methods=["POST"])
@login_required
def api_pause():
    return jsonify(player.pause())


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    return jsonify(player.stop())


@app.route("/api/next", methods=["POST"])
@login_required
def api_next():
    return jsonify(player.next())


@app.route("/api/previous", methods=["POST"])
@login_required
def api_previous():
    return jsonify(player.previous())


@app.route("/api/volume", methods=["POST"])
@login_required
def api_volume():
    data = request.get_json(silent=True) or {}
    vol = int(data.get("volume", 80))
    return jsonify(player.set_volume(vol))


@app.route("/api/seek", methods=["POST"])
@login_required
def api_seek():
    data = request.get_json(silent=True) or {}
    pos = float(data.get("position", 0))
    return jsonify(player.seek(pos))


@app.route("/api/loop", methods=["POST"])
@login_required
def api_loop():
    data = request.get_json(silent=True) or {}
    loop = bool(data.get("loop", True))
    return jsonify(player.set_loop(loop))


@app.route("/api/auto-play", methods=["POST"])
@login_required
def api_auto_play():
    data = request.get_json(silent=True) or {}
    auto_play = bool(data.get("auto_play", True))
    return jsonify(player.set_auto_play(auto_play))


# -- Status --

@app.route("/api/status")
@login_required
def api_status():
    return jsonify(player.status())


# -- Playlist --

@app.route("/api/playlist")
@login_required
def api_playlist():
    return jsonify({"playlist": player.get_playlist()})


@app.route("/api/playlist/reorder", methods=["POST"])
@login_required
def api_playlist_reorder():
    data = request.get_json(silent=True) or {}
    new_order = data.get("playlist", [])
    return jsonify(player.reorder_playlist(new_order))

@app.route("/api/playlist/add", methods=["POST"])
@login_required
def api_playlist_add():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename")
    if filename:
        return jsonify(player.add_video(filename))
    return jsonify({"error": "No filename"}), 400

@app.route("/api/playlist/update_item", methods=["POST"])
@login_required
def api_update_item():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename")
    active = data.get("active")
    schedule = data.get("schedule")
    return jsonify({"status": "ok", "playlist": player.update_item(filename, active, schedule)})


# -- File management --

@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type .{ext} not allowed"}), 400
    filename = secure_filename(f.filename)
    dest = VIDEO_DIR / filename
    f.save(str(dest))
    player.add_video(filename)
    return jsonify({"uploaded": filename, "playlist": player.get_playlist()})


@app.route("/api/delete", methods=["POST"])
@login_required
def api_delete():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    return jsonify(player.delete_video_file(filename))


@app.route("/api/videos")
@login_required
def api_videos():
    """List all video files on disk (may differ from playlist)."""
    files = []
    for f in sorted(VIDEO_DIR.iterdir()):
        if f.suffix.lstrip(".").lower() in ALLOWED_EXTENSIONS:
            size_mb = f.stat().st_size / (1024 * 1024)
            files.append({"name": f.name, "size_mb": round(size_mb, 2)})
    return jsonify({"videos": files})


# -- System info --

@app.route("/api/system-info")
@login_required
def api_system_info():
    """Return system information: disk, CPU, memory, temperature."""
    info = {}

    # Disk usage for video directory
    try:
        usage = shutil.disk_usage(str(VIDEO_DIR))
        info["disk"] = {
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "percent": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        info["disk"] = None

    # CPU usage (1-second sample)
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        info["cpu"] = {
            "load_1m": round(load1, 2),
            "load_5m": round(load5, 2),
            "load_15m": round(load15, 2),
            "cores": cpu_count,
            "percent": round(load1 / cpu_count * 100, 1),
        }
    except Exception:
        info["cpu"] = None

    # Memory usage from /proc/meminfo (Linux only)
    try:
        meminfo = Path("/proc/meminfo").read_text()
        mem = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                mem[parts[0].rstrip(":")] = int(parts[1])  # kB
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = total - available
        info["memory"] = {
            "total_mb": round(total / 1024, 1),
            "used_mb": round(used / 1024, 1),
            "free_mb": round(available / 1024, 1),
            "percent": round(used / total * 100, 1) if total else 0,
        }
    except Exception:
        info["memory"] = None

    # Raspberry Pi CPU temperature
    try:
        temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_path.exists():
            temp_c = int(temp_path.read_text().strip()) / 1000
            info["temperature"] = {"cpu_celsius": round(temp_c, 1)}
        else:
            info["temperature"] = None
    except Exception:
        info["temperature"] = None

    # Uptime
    try:
        uptime_sec = float(Path("/proc/uptime").read_text().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        minutes = int((uptime_sec % 3600) // 60)
        info["uptime"] = {
            "seconds": int(uptime_sec),
            "display": f"{days}d {hours}h {minutes}m",
        }
    except Exception:
        info["uptime"] = None

    return jsonify(info)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Signal handling for standalone mode
    def signal_handler(sig, frame):
        print("\nShutting down...")
        player.release()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 60)
    print("  Raspberry Pi Video Signage Player")
    print("  Open http://<raspi-ip>:5000 in your browser")
    print("=" * 60)
    # Use 0.0.0.0 so it's accessible from other devices on the network
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
else:
    # Logic for Gunicorn/WSGI
    # We use this to ensure cleanup when gunicorn kills workers
    import atexit
    atexit.register(player.release)
