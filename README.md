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

# Masuk ke direktori
cd ~/raspi-pyvlc-player

# Beri akses eksekusi dan jalankan installer
chmod +x install.sh
./install.sh
```

> **Catatan:** Selama instalasi berjalan, Anda akan diminta untuk memilih mode *Deployment*:
> 1. **Development**: Menggunakan `Flask built-in server` (Port 5000).
> 2. **Production**: Menggunakan `Gunicorn` + `Nginx` reverse proxy (Port 80) – Rekomendasi untuk pemakaian aslinya.
> 
> Anda juga akan diminta untuk mengatur **Username** dan **Password** yang aman untuk Dashboard.

### Manual

```bash
# 1. Install dependencies sistem
sudo apt-get update
sudo apt-get install -y vlc nginx python3 python3-venv python3-pip openssl

# 2. Buat Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install Python packages
pip install --upgrade pip
pip install -r requirements.txt gunicorn

# 4. Buat file environtment (.env)
echo "DASHBOARD_USER=admin" > .env
echo "DASHBOARD_PASSWORD=password_rahasia_anda" >> .env
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env

# 5. Jalankan
python app.py
```

## Cara Pakai

### Buka Web Control Panel

Tergantung pada spesifikasi mode instalasi yang dipilih:

- **Mode Production:** `http://<IP_RASPBERRY_PI>` (Port default 80)
- **Mode Development:** `http://<IP_RASPBERRY_PI>:5000`

Login menggunakan kredensial yang telah dibuat saat instalasi.

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

# Lihat log background (Troubleshooting)
journalctl -u signage-player -f
```

*(Jika menggunakan mode Production, Nginx command: `sudo systemctl status nginx`)*

## Lokasi File

| Lokasi | Keterangan |
|--------|------------|
| `.../raspi-pyvlc-player/videos/` | Folder tempat video disimpan |
| `.../raspi-pyvlc-player/.env` | Konfigurasi Dashboard login |
| `/etc/systemd/system/signage-player.service` | Konfigurasi Systemd service |
| `/etc/nginx/sites-available/signage-player` | Konfigurasi Reverse proxy (*Production mode*) |

## Format Video yang Didukung

- MP4, AVI, MKV, MOV, WMV, FLV, WebM, TS, M4V

## Troubleshooting

### Video tidak muncul
```bash
# Cek versi VLC
vlc --version

# Cek masalah player di background log
journalctl -u signage-player -n 50
```

### Tidak bisa akses web dashboard
```bash
# Cek apakah service berjalan
sudo systemctl status signage-player

# Cek ketersediaan Nginx (Milik production)
sudo systemctl status nginx
```

### Lupa Password Dashboard
Ubah file `.env` di dalam folder project, edit bagian `DASHBOARD_PASSWORD=` lalu restart service dengan `sudo systemctl restart signage-player`.

## Uninstall

```bash
# Hentikan dan hapus service
sudo systemctl stop signage-player
sudo systemctl disable signage-player
sudo rm /etc/systemd/system/signage-player.service

# (Opsional) Hapus konfigurasi Nginx jika digunakan
sudo rm -f /etc/nginx/sites-available/signage-player
sudo rm -f /etc/nginx/sites-enabled/signage-player
sudo systemctl restart nginx

# Reload ulang systemd daemon
sudo systemctl daemon-reload
```
