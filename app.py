#!/usr/bin/env python3
"""
Raspberry Pi Video Signage Player
Web-based control panel with python-vlc backend.
"""

import os
import json
import time
import threading
from pathlib import Path
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

import vlc
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from werkzeug.utils import secure_filename

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

# ---------------------------------------------------------------------------
# VLC Player Wrapper
# ---------------------------------------------------------------------------

class SignagePlayer:
    """Wraps python-vlc to manage a looping playlist for digital signage."""

    def __init__(self, video_dir: Path, playlist_file: Path):
        self.video_dir = video_dir
        self.playlist_file = playlist_file
        self.video_dir.mkdir(parents=True, exist_ok=True)

        # VLC instance - Reverted to a safer configuration to avoid flickering
        self.instance = vlc.Instance(
            "--fullscreen",
            "--no-video-title-show",
            "--no-osd",
            "--aout=alsa",
            "--mouse-hide-timeout=0",
            "--video-on-top",
            "--no-embedded-video" # Helps with some window managers to force fullscreen
        )
        self.list_player = self.instance.media_list_player_new()
        self.player = self.list_player.get_media_player()
        
        # Scaling to 0 (Original/Auto)
        self.player.video_set_scale(0)

        # Playlist state
        self.playlist: list[str] = []   # ordered list of filenames
        self.current_index: int = 0
        self.is_looping: bool = True

        # Lock for thread-safe operations
        self._lock = threading.Lock()

        # Load saved playlist (or scan directory)
        self._load_playlist()

        # Attach end-reached event so we can advance to the next video
        events = self.player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end_reached)

    # -- Playlist persistence ------------------------------------------------

    def _load_playlist(self):
        """Load playlist from JSON file, falling back to directory scan."""
        if self.playlist_file.exists():
            try:
                data = json.loads(self.playlist_file.read_text())
                self.playlist = [f for f in data.get("playlist", [])
                                 if (self.video_dir / f).is_file()]
                self.is_looping = data.get("loop", True)
            except (json.JSONDecodeError, KeyError):
                self.playlist = []
        if not self.playlist:
            self.playlist = self._scan_videos()
        self._save_playlist()
        self._rebuild_media_list()

    def _save_playlist(self):
        """Persist current playlist order to disk."""
        data = {"playlist": self.playlist, "loop": self.is_looping}
        self.playlist_file.write_text(json.dumps(data, indent=2))

    def _scan_videos(self) -> list[str]:
        """Return sorted list of video filenames in the video directory."""
        files = []
        for f in sorted(self.video_dir.iterdir()):
            if f.suffix.lstrip(".").lower() in ALLOWED_EXTENSIONS:
                files.append(f.name)
        return files

    def _rebuild_media_list(self):
        """Rebuild the VLC media list from self.playlist."""
        media_list = self.instance.media_list_new()
        for filename in self.playlist:
            path = str(self.video_dir / filename)
            media_list.add_media(self.instance.media_new(path))
        self.list_player.set_media_list(media_list)
        if self.is_looping:
            self.list_player.set_playback_mode(vlc.PlaybackMode.loop)
        else:
            self.list_player.set_playback_mode(vlc.PlaybackMode.default)

    # -- VLC event handlers --------------------------------------------------

    def _on_end_reached(self, event):
        """Called when a single media finishes playing."""
        with self._lock:
            if self.playlist:
                self.current_index = (self.current_index + 1) % len(self.playlist)

    # -- Transport controls --------------------------------------------------

    def play(self, index: int | None = None):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
            if index is not None:
                if 0 <= index < len(self.playlist):
                    self.current_index = index
                    self.list_player.play_item_at_index(index)
                else:
                    return {"error": "Index out of range"}
            else:
                self.list_player.play()
            
            return {"status": "playing"}

    def pause(self):
        self.list_player.pause()
        return {"status": "paused"}

    def stop(self):
        self.list_player.stop()
        return {"status": "stopped"}

    def next(self):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
            self.list_player.next()
            self.current_index = (self.current_index + 1) % len(self.playlist)
            return {"status": "next", "index": self.current_index}

    def previous(self):
        with self._lock:
            if not self.playlist:
                return {"error": "Playlist is empty"}
            self.list_player.previous()
            self.current_index = (self.current_index - 1) % len(self.playlist)
            return {"status": "previous", "index": self.current_index}

    def set_volume(self, vol: int):
        vol = max(0, min(150, vol))
        self.player.audio_set_volume(vol)
        return {"volume": vol}

    def seek(self, position: float):
        """Seek to position (0.0 – 1.0)."""
        position = max(0.0, min(1.0, position))
        self.player.set_position(position)
        return {"position": position}

    def set_loop(self, loop: bool):
        self.is_looping = loop
        if loop:
            self.list_player.set_playback_mode(vlc.PlaybackMode.loop)
        else:
            self.list_player.set_playback_mode(vlc.PlaybackMode.default)
        self._save_playlist()
        return {"loop": loop}

    # -- Status --------------------------------------------------------------

    def status(self) -> dict:
        state = str(self.player.get_state()).split(".")[-1]
        length = self.player.get_length()      # ms
        pos = self.player.get_position()        # 0.0-1.0
        current_time = self.player.get_time()   # ms
        volume = self.player.audio_get_volume()

        current_file = ""
        if self.playlist and 0 <= self.current_index < len(self.playlist):
            current_file = self.playlist[self.current_index]

        return {
            "state": state,
            "current_file": current_file,
            "current_index": self.current_index,
            "length": length,
            "time": current_time,
            "position": pos if pos >= 0 else 0,
            "volume": volume if volume >= 0 else 0,
            "loop": self.is_looping,
            "playlist_count": len(self.playlist),
        }

    # -- Playlist management -------------------------------------------------

    def get_playlist(self) -> list[str]:
        return list(self.playlist)

    def add_video(self, filename: str):
        """Add a video file to the end of the playlist."""
        with self._lock:
            if filename not in self.playlist:
                self.playlist.append(filename)
                self._save_playlist()
                self._rebuild_media_list()
        return {"playlist": self.playlist}

    def remove_video(self, filename: str):
        """Remove a video from the playlist."""
        with self._lock:
            if filename in self.playlist:
                idx = self.playlist.index(filename)
                self.playlist.remove(filename)
                if self.current_index >= len(self.playlist) and self.playlist:
                    self.current_index = 0
                self._save_playlist()
                self._rebuild_media_list()
        return {"playlist": self.playlist}

    def reorder_playlist(self, new_order: list[str]):
        """Replace playlist with a new ordering."""
        with self._lock:
            # Validate all files exist
            valid = [f for f in new_order if (self.video_dir / f).is_file()]
            self.playlist = valid
            self.current_index = 0
            self._save_playlist()
            self._rebuild_media_list()
        return {"playlist": self.playlist}

    def delete_video_file(self, filename: str):
        """Remove from playlist AND delete the file from disk."""
        self.remove_video(filename)
        filepath = self.video_dir / filename
        if filepath.is_file():
            filepath.unlink()
        return {"deleted": filename}


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
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == USERNAME and password == PASSWORD:
            session["logged_in"] = True
            session["user"] = username
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if username == USERNAME and password == PASSWORD:
        session["logged_in"] = True
        session["user"] = username
        return jsonify({"status": "ok"})
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Raspberry Pi Video Signage Player")
    print("  Open http://<raspi-ip>:5000 in your browser")
    print("=" * 60)
    # Use 0.0.0.0 so it's accessible from other devices on the network
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
