# Raspberry Pi Video Signage Player

Web-based video player signage control panel menggunakan Python VLC dan Flask.

## Fitur

- **Web Control Panel** - Kontrol player dari browser (HP/tablet/laptop)
- **Playlist Management** - Tambah, hapus, dan drag & drop urut ulang video
- **Video Scheduling** - Atur rentang tanggal dan jam tayang per video, serta fitur aktif/nonaktif
- **Auto-loop & Auto-play** - Playlist otomatis berputar & auto-play saat boot
- **System Monitoring** - Pantau CPU, Memory, Disk, dan Suhu secara real-time
- **Security** - Login aman (Password Hash), CSRF protection, & brute-force rate limit
- **Upload Video** - Upload langsung dari browser dan drag & drop (max 2GB)
- **Fullscreen Playback** - Video diputar fullscreen di monitor Raspberry Pi

## Spesifikasi

- **Platform:** Raspberry Pi (Raspberry Pi OS)
- **Player:** VLC (python-vlc)
- **Backend:** Flask (Python)
- **Frontend:** HTML/CSS/JavaScript (Dark theme)
- **Akses:** http://<IP_RASPBERRY_PI>:80

## Installasi

### Otomatis (Recommended)

```bash
# Copy project ke Raspberry Pi
scp -r raspi-pyvlc-player/ pi@<IP_RASPBERRY_PI>:~/

# Login ke Raspberry Pi
ssh pi@<IP_RASPBERRY_PI>

# Jalankan installer
cd ~/raspi-pyvlc-player
chmod +x install.sh
./install.sh
```

### Manual

```bash
# Install dependencies
sudo apt-get update
sudo apt-get install -y vlc python3 python3-venv python3-pip

# Buat virtualenv
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install flask python-vlc

# Jalankan
python app.py
```

## Cara Pakai

### Buka Web Control Panel

```
http://<IP_RASPBERRY_PI>:80
```

### Upload Video

1. Klik zone upload atau drag & drop file video
2. Video akan otomatis masuk playlist
3. Klik play untuk memulai

### Kontrol Player

| Tombol | Fungsi |
|--------|--------|
| ▶️ | Play / Pause |
| ⏹️ | Stop |
| ⏭️ | Next video |
| ⏮️ | Previous video |
| 🔁 | Toggle loop |
| Volume | Atur volume |

### Manajemen Playlist

- Klik nama video untuk langsung play
- Klik icon X untuk hapus dari playlist
- Drag & drop untuk urut ulang

## Service Commands

```bash
# Status
sudo systemctl status signage-player

# Restart
sudo systemctl restart signage-player

# Stop
sudo systemctl stop signage-player

# Lihat log
journalctl -u signage-player -f
```

## Lokasi File

| Lokasi | Keterangan |
|--------|------------|
| `/home/pi/raspi-pyvlc-player/videos/` | Folder video |
| `/home/pi/raspi-pyvlc-player/playlist.json` | Playlist tersimpan |
| `/etc/systemd/system/signage-player.service` | Systemd service |

## Format Video yang Didukung

- MP4
- AVI
- MKV
- MOV
- WMV
- FLV
- WebM
- TS
- M4V

## Troubleshooting

### Video tidak muncul

```bash
# Cek apakah VLC terinstall
vlc --version

# Cek log
journalctl -u signage-player -n 50
```

### Tidak bisa akses web

```bash
# Cek service berjalan
sudo systemctl status signage-player

# Cek port 80
sudo netstat -tlnp | grep 80
```

### Suara tidak keluar

```bash
# Cek output audio VLC
# Edit app.py dan ubah parameter aout:
# --aout=alsa (default)
# atau --aout=pulse untuk PulseAudio
```

## Uninstall

```bash
sudo systemctl stop signage-player
sudo systemctl disable signage-player
sudo rm /etc/systemd/system/signage-player.service
sudo systemctl daemon-reload
```
