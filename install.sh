#!/bin/bash
# ============================================================
#  Signage Player  –  Install & Setup Script for Raspberry Pi
# ============================================================

set -e

# --- Constants & Variables ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="signage-player"
REQUIRED_PACKAGES="vlc nginx python3 python3-venv python3-pip openssl"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# --- Helper Functions ---
print_step() {
    echo -e "\n${CYAN}==>${NC} ${GREEN}$1${NC}"
}

print_info() {
    echo -e "${YELLOW}INFO:${NC} $1"
}

print_error() {
    echo -e "${RED}ERROR:${NC} $1"
}

command_exists() {
    command -v "$1" &> /dev/null
}

package_installed() {
    dpkg -l "$1" 2> /dev/null | grep -q "^ii"
}

# --- Main Script ---
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}         Signage Player Installer           ${NC}"
echo -e "${CYAN}============================================${NC}"

# 1. Ask for deployment mode
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
    print_info "Production mode selected. Nginx + Gunicorn will be installed."
else
    PRODUCTION_MODE=false
    print_info "Development mode selected."
fi

# 2. Ask for credentials
echo ""
echo -e "${CYAN}--- Dashboard Credentials Setup ---${NC}"
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
        print_error "Passwords do not match. Please try again."
    fi
done

echo ""
print_info "Credentials set to Username: ${DASHBOARD_USER}"

# 3. Check and install system dependencies
print_step "[1/6] Checking system dependencies..."
MISSING_PACKAGES=""

for pkg in $REQUIRED_PACKAGES; do
    case $pkg in
        vlc)
            if ! command_exists vlc; then MISSING_PACKAGES="$MISSING_PACKAGES vlc"; fi
            ;;
        python3)
            if ! command_exists python3; then MISSING_PACKAGES="$MISSING_PACKAGES python3"; fi
            ;;
        python3-venv)
            if ! python3 -m venv --help &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES python3-venv"; fi
            ;;
        python3-pip)
            if ! command_exists pip3 && ! python3 -m pip --version &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES python3-pip"; fi
            ;;
        openssl)
            if ! command_exists openssl; then MISSING_PACKAGES="$MISSING_PACKAGES openssl"; fi
            ;;
        nginx)
            if ! command_exists nginx; then MISSING_PACKAGES="$MISSING_PACKAGES nginx"; fi
            ;;
        *)
            if ! package_installed "$pkg"; then MISSING_PACKAGES="$MISSING_PACKAGES $pkg"; fi
            ;;
    esac
done

if [ -n "$MISSING_PACKAGES" ]; then
    print_info "Missing packages detected: $MISSING_PACKAGES"
    print_info "Installing missing dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y $MISSING_PACKAGES
else
    print_info "All system dependencies are satisfied."
fi

# 4. Create virtual environment
print_step "[2/6] Creating Python virtual environment..."
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
    print_info "Virtual environment created at $SCRIPT_DIR/venv"
else
    print_info "Virtual environment already exists."
fi

# 5. Install Python dependencies
print_step "[3/6] Installing Python packages..."
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

if [ "$PRODUCTION_MODE" = true ]; then
    print_info "Installing Gunicorn for production..."
    "$SCRIPT_DIR/venv/bin/pip" install gunicorn
fi

# 6. Create videos directory & setup environment
print_step "[4/6] Setting up application environment..."
mkdir -p "$SCRIPT_DIR/videos"
print_info "Created videos directory."

print_info "Generating .env file with secret key..."
SECRET_KEY=$(openssl rand -hex 32)
cat > "$SCRIPT_DIR/.env" << EOF
# Dashboard Authentication
DASHBOARD_USER=${DASHBOARD_USER}
DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}

# Flask Secret Key (auto-generated)
SECRET_KEY=${SECRET_KEY}
EOF
print_info ".env file created securely."

# 7. Install systemd service
print_step "[5/6] Configuration systemd service..."
chmod +x "$SCRIPT_DIR/run.sh"

if [ "$PRODUCTION_MODE" = true ]; then
    # Production: Gunicorn service
    sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Raspberry Pi Video Signage Player (Gunicorn)
After=network.target graphical.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/bin/bash ${SCRIPT_DIR}/run.sh ${SCRIPT_DIR}/venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5000 app:app
Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
else
    # Development: Flask built-in server
    sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Raspberry Pi Video Signage Player
After=network.target graphical.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/bin/bash ${SCRIPT_DIR}/run.sh ${SCRIPT_DIR}/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
fi

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

# 8. Configure Nginx (production mode only)
print_step "[6/6] Configuring Nginx web server..."
if [ "$PRODUCTION_MODE" = true ]; then
    sudo mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
    sudo tee /etc/nginx/sites-available/${SERVICE_NAME} > /dev/null <<EOF
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
    print_info "Skipping Nginx configuration (Development mode)."
    PORT_DISPLAY="5000"
fi

# 9. Start service
print_info "Starting $SERVICE_NAME service..."
sudo systemctl restart ${SERVICE_NAME}

IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "\n${GREEN}============================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${GREEN}============================================${NC}\n"

if [ "$PRODUCTION_MODE" = true ]; then
    echo -e "  Mode: ${CYAN}PRODUCTION (Gunicorn + Nginx)${NC}"
else
    echo -e "  Mode: ${CYAN}DEVELOPMENT (Flask built-in)${NC}"
fi

echo -e "\n  ${YELLOW}Credentials:${NC}"
echo -e "    Username: ${DASHBOARD_USER}"
echo -e "    Password: ********"

echo -e "\n  ${YELLOW}Open in browser:${NC}"
echo -e "    http://${IP_ADDR}:${PORT_DISPLAY}"

echo -e "\n  ${YELLOW}Service commands:${NC}"
echo -e "    sudo systemctl status  ${SERVICE_NAME}"
echo -e "    sudo systemctl restart ${SERVICE_NAME}"
echo -e "    sudo systemctl stop    ${SERVICE_NAME}"
echo -e "    journalctl -u ${SERVICE_NAME} -f"

if [ "$PRODUCTION_MODE" = true ]; then
    echo -e "\n  ${YELLOW}Nginx commands:${NC}"
    echo -e "    sudo systemctl status  nginx"
    echo -e "    sudo systemctl restart nginx"
fi

echo -e "\n  ${YELLOW}Put your video files in:${NC}"
echo -e "    ${SCRIPT_DIR}/videos/"
echo -e "${GREEN}============================================${NC}\n"
