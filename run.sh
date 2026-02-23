#!/bin/bash
# Script to auto-detect display and wait for GUI to be ready before starting

export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DISPLAY=:0

echo "Waiting for Graphical Interface to be ready..."
for i in {1..30}; do
    if [ -S "$XDG_RUNTIME_DIR/wayland-1" ]; then
        export WAYLAND_DISPLAY=wayland-1
        echo "Found Wayland display: wayland-1"
        break
    elif [ -S "$XDG_RUNTIME_DIR/wayland-0" ]; then
        export WAYLAND_DISPLAY=wayland-0
        echo "Found Wayland display: wayland-0"
        break
    elif [ -e "/tmp/.X11-unix/X0" ]; then
        echo "Found X11 display: :0"
        break
    fi
    sleep 2
done

# Beri jeda ekstra beberapa detik untuk memastikan *desktop compositor* selesai sepenuhnya
sleep 5

echo "Starting Application..."
exec "$@"
