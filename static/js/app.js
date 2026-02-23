/* ===================================================================
   Signage Player Control Panel  –  JavaScript
   =================================================================== */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function apiCall(url, method = "GET", body = null) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const opts = { method, headers: { "X-CSRF-Token": csrfToken } };
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
let isAutoPlay = true;
let isSeeking = false;

// DOM refs
const statusBadge = document.getElementById("statusBadge");
const currentTrack = document.getElementById("currentTrack");
const currentTimeEl = document.getElementById("currentTime");
const totalTimeEl = document.getElementById("totalTime");
const seekBar = document.getElementById("seekBar");
const volumeBar = document.getElementById("volumeBar");
const volumeValue = document.getElementById("volumeValue");
const playIcon = document.getElementById("playIcon");
const pauseIcon = document.getElementById("pauseIcon");
const loopBtn = document.getElementById("loopBtn");
const autoPlayBtn = document.getElementById("autoPlayBtn");
const playlistContainer = document.getElementById("playlistContainer");
const playlistCount = document.getElementById("playlistCount");
const filesList = document.getElementById("filesList");
const uploadZone = document.getElementById("uploadZone");
const fileInput = document.getElementById("fileInput");
const uploadProgress = document.getElementById("uploadProgress");
const progressFill = document.getElementById("progressFill");
const progressText = document.getElementById("progressText");

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
    totalTimeEl.textContent = formatTime(data.length);

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

    // Auto-play
    isAutoPlay = data.auto_play;
    autoPlayBtn.classList.toggle("active", isAutoPlay);
}

setInterval(pollStatus, 1000);
pollStatus();

// ---------------------------------------------------------------------------
// System info polling
// ---------------------------------------------------------------------------

async function pollSystemInfo() {
    const data = await apiCall("/api/system-info");
    if (data.error) return;

    // CPU
    if (data.cpu) {
        const cpuPct = Math.min(data.cpu.percent, 100);
        const cpuBar = document.getElementById("cpuBar");
        cpuBar.style.width = cpuPct + "%";
        cpuBar.className = "sys-bar-fill" + (cpuPct > 80 ? " danger" : cpuPct > 60 ? " warn" : "");
        document.getElementById("cpuValue").textContent = cpuPct + "%";
    }

    // Memory
    if (data.memory) {
        const memBar = document.getElementById("memBar");
        memBar.style.width = data.memory.percent + "%";
        memBar.className = "sys-bar-fill sys-bar-memory" + (data.memory.percent > 85 ? " danger" : "");
        document.getElementById("memValue").textContent = data.memory.percent + "% (" + Math.round(data.memory.used_mb) + " MB)";
    }

    // Disk
    if (data.disk) {
        const diskBar = document.getElementById("diskBar");
        diskBar.style.width = data.disk.percent + "%";
        diskBar.className = "sys-bar-fill sys-bar-disk" + (data.disk.percent > 90 ? " danger" : "");
        document.getElementById("diskValue").textContent = data.disk.percent + "% (" + data.disk.free_gb + " GB free)";
    }

    // Temperature
    if (data.temperature) {
        const temp = data.temperature.cpu_celsius;
        const tempEl = document.getElementById("tempValue");
        tempEl.textContent = temp + "°C";
        tempEl.style.color = temp > 70 ? "var(--red)" : temp > 55 ? "var(--orange)" : "var(--green)";
    }

    // Uptime
    if (data.uptime) {
        document.getElementById("sysUptime").textContent = "Uptime: " + data.uptime.display;
    }
}

setInterval(pollSystemInfo, 5000);
pollSystemInfo();

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

function toggleAutoPlay() {
    isAutoPlay = !isAutoPlay;
    apiCall("/api/auto-play", "POST", { auto_play: isAutoPlay });
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

let dragSrcIndex = null;

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

    playlistContainer.innerHTML = list.map((item, i) => `
        <div class="playlist-item ${i === activeIndex ? 'active' : ''} ${!item.active ? 'disabled' : ''}"
             draggable="true"
             data-index="${i}"
             data-filename="${item.filename}"
             onclick="playIndex(${i})">
            <span class="drag-handle" title="Drag to reorder">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/>
                    <circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/>
                    <circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/>
                </svg>
            </span>
            <span class="track-index">${i + 1}</span>
            <span class="track-name">${item.filename}</span>
            <div class="track-actions">
                <button class="btn-icon" onclick="event.stopPropagation(); openScheduleModal('${item.filename}')" title="Settings">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="3"></circle>
                        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                    </svg>
                </button>
                <button class="btn-icon danger" onclick="event.stopPropagation(); removeFromPlaylist('${item.filename}')" title="Remove">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
        </div>
    `).join("");

    // Attach drag events to playlist items
    playlistContainer.querySelectorAll(".playlist-item").forEach(item => {
        item.addEventListener("dragstart", onDragStart);
        item.addEventListener("dragover", onDragOver);
        item.addEventListener("dragenter", onDragEnter);
        item.addEventListener("dragleave", onDragLeave);
        item.addEventListener("drop", onDrop);
        item.addEventListener("dragend", onDragEnd);
    });
}

function onDragStart(e) {
    dragSrcIndex = parseInt(this.dataset.index);
    this.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragSrcIndex);
}

function onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
}

function onDragEnter(e) {
    e.preventDefault();
    this.classList.add("drag-over-item");
}

function onDragLeave() {
    this.classList.remove("drag-over-item");
}

async function onDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    this.classList.remove("drag-over-item");
    const targetIndex = parseInt(this.dataset.index);
    if (dragSrcIndex === null || dragSrcIndex === targetIndex) return;

    // Get current playlist and reorder
    const data = await apiCall("/api/playlist");
    const list = data.playlist || [];
    const [moved] = list.splice(dragSrcIndex, 1);
    list.splice(targetIndex, 0, moved);

    await apiCall("/api/playlist/reorder", "POST", { playlist: list });
    loadPlaylist();
}

function onDragEnd() {
    this.classList.remove("dragging");
    playlistContainer.querySelectorAll(".playlist-item").forEach(item => {
        item.classList.remove("drag-over-item");
    });
    dragSrcIndex = null;
}

async function playIndex(index) {
    await apiCall("/api/play", "POST", { index });
    setTimeout(loadPlaylist, 300);
}

async function removeFromPlaylist(filename) {
    const data = await apiCall("/api/playlist");
    const list = data.playlist || [];
    await apiCall("/api/playlist/reorder", "POST", {
        playlist: list.filter(item => item.filename !== filename)
    });
    loadPlaylist();
}

let loadedPlaylistData = [];

// Cache the playlist response
async function fetchAndCachePlaylist() {
    const data = await apiCall("/api/playlist");
    if (data.error) return;
    loadedPlaylistData = data.playlist || [];
    return loadedPlaylistData;
}

// Ensure the render function uses the cache function
const originalLoadPlaylist = loadPlaylist;
loadPlaylist = async function () {
    await fetchAndCachePlaylist();
    // We already fetch inside loadPlaylist but we just hijacked it to also do caching.
    return originalLoadPlaylist.apply(this, arguments);
};

// ---------------------------------------------------------------------------
// Schedule Modal
// ---------------------------------------------------------------------------

function openScheduleModal(filename) {
    const item = loadedPlaylistData.find(f => f.filename === filename);
    if (!item) return;

    document.getElementById("schedFilename").value = item.filename;
    document.getElementById("modalTitle").textContent = "Schedule: " + item.filename;
    document.getElementById("schedActive").checked = item.active !== false;

    const sched = item.schedule || {};
    document.getElementById("schedStartDate").value = sched.start_date || "";
    document.getElementById("schedEndDate").value = sched.end_date || "";
    document.getElementById("schedStartTime").value = sched.start_time || "";
    document.getElementById("schedEndTime").value = sched.end_time || "";

    document.getElementById("scheduleModal").classList.add("active");
}

function closeScheduleModal() {
    document.getElementById("scheduleModal").classList.remove("active");
}

async function saveSchedule() {
    const filename = document.getElementById("schedFilename").value;
    const active = document.getElementById("schedActive").checked;

    const schedule = {
        start_date: document.getElementById("schedStartDate").value,
        end_date: document.getElementById("schedEndDate").value,
        start_time: document.getElementById("schedStartTime").value,
        end_time: document.getElementById("schedEndTime").value
    };

    await apiCall("/api/playlist/update_item", "POST", {
        filename,
        active,
        schedule
    });

    closeScheduleModal();
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
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
        xhr.setRequestHeader("X-CSRF-Token", csrfToken);
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
            <div class="file-actions">
               <button class="btn btn-sm btn-primary" onclick="addToPlaylist('${v.name}')" title="Add to Playlist">+</button>
               <button class="btn btn-sm btn-danger" onclick="deleteFile('${v.name}')">Delete</button>
            </div>
        </div>
    `).join("");
}

async function addToPlaylist(filename) {
    await apiCall("/api/playlist/add", "POST", { filename });
    loadPlaylist();
}

async function deleteFile(filename) {
    if (!confirm(`Delete "${filename}" permanently?`)) return;
    await apiCall("/api/delete", "POST", { filename });
    loadPlaylist();
    refreshFiles();
}

refreshFiles();
