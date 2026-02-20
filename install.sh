#!/bin/bash
# ============================================================
#  Signage Player  –  Install & Setup Script for Raspberry Pi
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="signage-player"

# Required system packages
REQUIRED_PACKAGES="vlc python3 python3-venv python3-pip openssl"

echo "============================================"
echo "  Signage Player Installer"
echo "============================================"

# Ask for deployment mode
echo ""
echo "Select deployment mode:"
echo "  1) Development (Flask built-in server, port 5000)"
echo "  2) Production (Gunicorn + Nginx, port 80)"
echo ""
read -p "Enter choice [1-2] (default: 1): " DEPLOY_MODE
DEPLOY_MODE=${DEPLOY_MODE:-1}

if [ "$DEPLOY_MODE" = "2" ]; then
    PRODUCTION_MODE=true
    REQUIRED_PACKAGES="$REQUIRED_PACKAGES nginx"
    echo ""
    echo "Production mode selected. Nginx + Gunicorn will be installed."
else
    PRODUCTION_MODE=false
    echo ""
    echo "Development mode selected."
fi

# Ask for credentials
echo ""
echo "============================================"
echo "  Dashboard Credentials Setup"
echo "============================================"
echo ""

read -p "Enter username (default: admin): " INPUT_USER
DASHBOARD_USER=${INPUT_USER:-admin}

while true; do
    read -s -p "Enter password (default: admin): " INPUT_PASS
    echo ""
    if [ -z "$INPUT_PASS" ]; then
        DASHBOARD_PASSWORD="admin"
        break
    fi
    read -s -p "Confirm password: " INPUT_PASS_CONFIRM
    echo ""
    if [ "$INPUT_PASS" = "$INPUT_PASS_CONFIRM" ]; then
        DASHBOARD_PASSWORD="$INPUT_PASS"
        break
    else
        echo "Passwords do not match. Please try again."
    fi
done

echo ""
echo "Credentials set:"
echo "  Username: $DASHBOARD_USER"
echo "  Password: ********"

# Function to check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Function to check if Debian package is installed
package_installed() {
    dpkg -l "$1" 2> /dev/null | grep -q "^ii"
}

# 1. Check and install system dependencies
echo "[1/5] Checking system dependencies..."

MISSING_PACKAGES=""

for pkg in $REQUIRED_PACKAGES; do
    case $pkg in
        vlc)
            if ! command_exists vlc; then
                MISSING_PACKAGES="$MISSING_PACKAGES vlc"
            fi
            ;;
        python3)
            if ! command_exists python3; then
                MISSING_PACKAGES="$MISSING_PACKAGES python3"
            fi
            ;;
        python3-venv)
            if ! python3 -m venv --help &> /dev/null; then
                MISSING_PACKAGES="$MISSING_PACKAGES python3-venv"
            fi
            ;;
        python3-pip)
            if ! command_exists pip3 && ! python3 -m pip --version &> /dev/null; then
                MISSING_PACKAGES="$MISSING_PACKAGES python3-pip"
            fi
            ;;
        openssl)
            if ! command_exists openssl; then
                MISSING_PACKAGES="$MISSING_PACKAGES openssl"
            fi
            ;;
    esac
done

if [ -n "$MISSING_PACKAGES" ]; then
    echo "Missing packages detected:$MISSING_PACKAGES"
    echo "Installing missing dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y $MISSING_PACKAGES
else
    echo "All system dependencies are satisfied."
fi

# 2. Create virtual environment
echo "[2/5] Creating Python virtual environment..."
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
fi

# 3. Install Python dependencies
echo "[3/6] Installing Python packages..."
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

if [ "$PRODUCTION_MODE" = true ]; then
    echo "Installing Gunicorn..."
    "$SCRIPT_DIR/venv/bin/pip" install gunicorn
fi

# 4. Create videos directory & setup environment
echo "[4/6] Creating videos directory and environment..."
mkdir -p "$SCRIPT_DIR/videos"

echo "Creating .env file with generated secret key..."
SECRET_KEY=$(openssl rand -hex 32)
cat > "$SCRIPT_DIR/.env" << EOF
# Dashboard Authentication
DASHBOARD_USER=${DASHBOARD_USER}
DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}

# Flask Secret Key (auto-generated)
SECRET_KEY=${SECRET_KEY}
EOF
echo ".env file created successfully."

# 5. Install systemd service
echo "[5/6] Installing systemd service..."

if [ "$PRODUCTION_MODE" = true ]; then
    # Production: Gunicorn service
    sudo bash -c "cat > /etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Raspberry Pi Video Signage Player (Gunicorn)
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${SCRIPT_DIR}
Environment=DISPLAY=:0
ExecStart=${SCRIPT_DIR}/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5000 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
else
    # Development: Flask built-in server
    sudo bash -c "cat > /etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Raspberry Pi Video Signage Player
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${SCRIPT_DIR}
Environment=DISPLAY=:0
ExecStart=${SCRIPT_DIR}/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
fi

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

# 6. Configure Nginx (production mode only)
if [ "$PRODUCTION_MODE" = true ]; then
    echo "[6/6] Configuring Nginx..."
    
    sudo bash -c "cat > /etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        client_max_body_size 2G;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
        proxy_read_timeout 300;
    }
}
EOF

    sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo systemctl restart nginx
    
    PORT_DISPLAY="80 (via Nginx)"
else
    echo "[6/6] Skipping Nginx configuration..."
    PORT_DISPLAY="5000"
fi

sudo systemctl start ${SERVICE_NAME}

IP_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo "============================================"
echo "  Installation complete!"
echo ""
if [ "$PRODUCTION_MODE" = true ]; then
    echo "  Mode: PRODUCTION (Gunicorn + Nginx)"
else
    echo "  Mode: DEVELOPMENT (Flask built-in)"
fi
echo ""
echo "  Credentials:"
echo "    Username: ${DASHBOARD_USER}"
echo "    Password: ********"
echo ""
echo "  Open in browser:"
echo "    http://${IP_ADDR}:${PORT_DISPLAY}"
echo ""
echo "  Service commands:"
echo "    sudo systemctl status  ${SERVICE_NAME}"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo "    sudo systemctl stop    ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
if [ "$PRODUCTION_MODE" = true ]; then
    echo ""
    echo "  Nginx commands:"
    echo "    sudo systemctl status  nginx"
    echo "    sudo systemctl restart nginx"
fi
echo ""
echo "  Put your video files in:"
echo "    ${SCRIPT_DIR}/videos/"
echo "============================================"
