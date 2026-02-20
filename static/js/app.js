/* ===================================================================
   Signage Player Control Panel  –  JavaScript
   =================================================================== */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function apiCall(url, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
    }
    try {
        const res = await fetch(url, opts);
        if (res.status === 401 || res.status === 302) {
            window.location.href = "/login";
            return { error: "Unauthorized" };
        }
        return await res.json();
    } catch (err) {
        console.error("API error:", err);
        return { error: err.message };
    }
}

function formatTime(ms) {
    if (!ms || ms < 0) return "00:00";
    const totalSec = Math.floor(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentState = "Stopped";
let isLooping = true;
let isSeeking = false;

// DOM refs
const statusBadge     = document.getElementById("statusBadge");
const currentTrack    = document.getElementById("currentTrack");
const currentTimeEl   = document.getElementById("currentTime");
const totalTimeEl     = document.getElementById("totalTime");
const seekBar         = document.getElementById("seekBar");
const volumeBar       = document.getElementById("volumeBar");
const volumeValue     = document.getElementById("volumeValue");
const playIcon        = document.getElementById("playIcon");
const pauseIcon       = document.getElementById("pauseIcon");
const loopBtn         = document.getElementById("loopBtn");
const playlistContainer = document.getElementById("playlistContainer");
const playlistCount   = document.getElementById("playlistCount");
const filesList       = document.getElementById("filesList");
const uploadZone      = document.getElementById("uploadZone");
const fileInput       = document.getElementById("fileInput");
const uploadProgress  = document.getElementById("uploadProgress");
const progressFill    = document.getElementById("progressFill");
const progressText    = document.getElementById("progressText");

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

async function pollStatus() {
    const data = await apiCall("/api/status");
    if (data.error) return;

    // State badge
    currentState = data.state;
    statusBadge.textContent = data.state;
    statusBadge.className = "status-badge " + data.state.toLowerCase();

    // Play/Pause icon
    if (data.state === "Playing") {
        playIcon.style.display = "none";
        pauseIcon.style.display = "block";
    } else {
        playIcon.style.display = "block";
        pauseIcon.style.display = "none";
    }

    // Current track
    currentTrack.textContent = data.current_file || "No video loaded";

    // Time
    currentTimeEl.textContent = formatTime(data.time);
    totalTimeEl.textContent   = formatTime(data.length);

    // Seek bar (don't update while user is dragging)
    if (!isSeeking) {
        seekBar.value = Math.round((data.position || 0) * 1000);
    }

    // Volume
    volumeBar.value = data.volume;
    volumeValue.textContent = data.volume + "%";

    // Loop
    isLooping = data.loop;
    loopBtn.classList.toggle("active", isLooping);
}

setInterval(pollStatus, 1000);
pollStatus();

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

function togglePlayPause() {
    if (currentState === "Playing") {
        apiCall("/api/pause", "POST");
    } else {
        apiCall("/api/play", "POST");
    }
}

function toggleLoop() {
    isLooping = !isLooping;
    apiCall("/api/loop", "POST", { loop: isLooping });
}

function setVolume(val) {
    volumeValue.textContent = val + "%";
    apiCall("/api/volume", "POST", { volume: parseInt(val) });
}

// Seek bar interaction
seekBar.addEventListener("mousedown", () => { isSeeking = true; });
seekBar.addEventListener("touchstart", () => { isSeeking = true; });
seekBar.addEventListener("mouseup", () => {
    isSeeking = false;
    apiCall("/api/seek", "POST", { position: seekBar.value / 1000 });
});
seekBar.addEventListener("touchend", () => {
    isSeeking = false;
    apiCall("/api/seek", "POST", { position: seekBar.value / 1000 });
});

// ---------------------------------------------------------------------------
// Playlist
// ---------------------------------------------------------------------------

async function loadPlaylist() {
    const data = await apiCall("/api/playlist");
    if (data.error) return;
    const list = data.playlist || [];
    playlistCount.textContent = list.length;

    if (list.length === 0) {
        playlistContainer.innerHTML = '<p class="empty-msg">No videos in playlist</p>';
        return;
    }

    const statusData = await apiCall("/api/status");
    const activeIndex = statusData.current_index || 0;

    playlistContainer.innerHTML = list.map((file, i) => `
        <div class="playlist-item ${i === activeIndex ? 'active' : ''}" onclick="playIndex(${i})">
            <span class="track-index">${i + 1}</span>
            <span class="track-name">${file}</span>
            <button class="btn-remove" onclick="event.stopPropagation(); removeFromPlaylist('${file}')" title="Remove">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        </div>
    `).join("");
}

async function playIndex(index) {
    await apiCall("/api/play", "POST", { index });
    setTimeout(loadPlaylist, 300);
}

async function removeFromPlaylist(filename) {
    await apiCall("/api/playlist/reorder", "POST", {
        playlist: (await apiCall("/api/playlist")).playlist.filter(f => f !== filename)
    });
    loadPlaylist();
}

loadPlaylist();
setInterval(loadPlaylist, 5000);

// ---------------------------------------------------------------------------
// File upload
// ---------------------------------------------------------------------------

uploadZone.addEventListener("click", () => fileInput.click());
uploadZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadZone.classList.add("drag-over");
});
uploadZone.addEventListener("dragleave", () => {
    uploadZone.classList.remove("drag-over");
});
uploadZone.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFiles(fileInput.files);
});

async function uploadFiles(files) {
    for (const file of files) {
        await uploadSingleFile(file);
    }
    loadPlaylist();
    refreshFiles();
}

function uploadSingleFile(file) {
    return new Promise((resolve) => {
        const formData = new FormData();
        formData.append("file", file);

        const xhr = new XMLHttpRequest();
        uploadProgress.style.display = "flex";

        xhr.upload.addEventListener("progress", (e) => {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                progressFill.style.width = pct + "%";
                progressText.textContent = pct + "%";
            }
        });

        xhr.addEventListener("load", () => {
            progressFill.style.width = "100%";
            progressText.textContent = "Done!";
            setTimeout(() => {
                uploadProgress.style.display = "none";
                progressFill.style.width = "0%";
            }, 1500);
            resolve();
        });

        xhr.addEventListener("error", () => {
            progressText.textContent = "Error!";
            setTimeout(() => { uploadProgress.style.display = "none"; }, 2000);
            resolve();
        });

        xhr.open("POST", "/api/upload");
        xhr.send(formData);
    });
}

// ---------------------------------------------------------------------------
// Files on disk
// ---------------------------------------------------------------------------

async function refreshFiles() {
    const data = await apiCall("/api/videos");
    if (data.error) return;
    const videos = data.videos || [];

    if (videos.length === 0) {
        filesList.innerHTML = '<p class="empty-msg">No files</p>';
        return;
    }

    filesList.innerHTML = videos.map(v => `
        <div class="file-item">
            <span class="file-name">${v.name}</span>
            <span class="file-size">${v.size_mb} MB</span>
            <button class="btn btn-sm btn-danger" onclick="deleteFile('${v.name}')">Delete</button>
        </div>
    `).join("");
}

async function deleteFile(filename) {
    if (!confirm(`Delete "${filename}" permanently?`)) return;
    await apiCall("/api/delete", "POST", { filename });
    loadPlaylist();
    refreshFiles();
}

refreshFiles();
