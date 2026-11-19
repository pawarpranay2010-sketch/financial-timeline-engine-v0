#!/data/data/com.termux/files/usr/bin/sh
# Platrixa — Termux:Boot startup script (S2 + M7).
#
# Installed to: ~/.termux/boot/platrixa-worker-boot.sh
#
# ONE runsvdir supervises EVERY platrixa-worker* service. This script starts
# that single supervisor and brings up each installed instance. It never starts
# a second runsvdir tree: two supervisors over the same service directory
# would race to restart the same worker.
#
# HONEST LIMITS
#   * This does NOT start the Android model app. The model app has no
#     documented boot receiver, so its server must be started manually after a
#     reboot. Each run script waits for the endpoint and each worker gates
#     every claim, so a late model shows up as "not ready", not as failed jobs.
#   * Termux:Boot must be installed and Termux launched once after installing
#     it; see device/termux/README.md.

set -u

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOME_DIR="${HOME:?HOME must be set}"
LOG_DIR="${PLATRIXA_LOG_DIR:-$HOME_DIR/.platrixa/logs}"
SERVICE_DIR="$PREFIX/var/service"

export PATH="$PREFIX/bin:$PATH"
mkdir -p "$LOG_DIR"

# Keep the CPU awake while workers are supervised, when termux-api is present.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
fi

# Start runsvdir once. `pgrep -f` is used only for this supervisor check; no
# worker is ever matched, signalled or killed by name here.
if ! pgrep -f "runsvdir $SERVICE_DIR" >/dev/null 2>&1; then
    nohup runsvdir -P "$SERVICE_DIR" >>"$LOG_DIR/runsvdir.log" 2>&1 &
fi

# Give runit a moment to notice the service directories.
sleep 2

if command -v sv >/dev/null 2>&1; then
    found=0
    for service in "$SERVICE_DIR"/platrixa-worker*; do
        [ -d "$service" ] || continue
        found=1
        name="$(basename "$service")"
        sv up "$name" >>"$LOG_DIR/runsvdir.log" 2>&1 || true
    done
    if [ "$found" = "0" ]; then
        echo "platrixa: no platrixa-worker* service installed under $SERVICE_DIR" \
            >>"$LOG_DIR/runsvdir.log"
    fi
fi
