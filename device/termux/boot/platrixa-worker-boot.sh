#!/data/data/com.termux/files/usr/bin/sh
# Platrixa — Termux:Boot startup script (S2).
#
# Installed to: ~/.termux/boot/platrixa-worker-boot.sh
#
# Termux:Boot runs this after the device boots AND the user has unlocked the
# first time. It starts the user's runit supervisor and brings the worker
# service up.
#
# HONEST LIMITS
#   * This does NOT start the Android model app. The model app has no
#     documented boot receiver, so its server must be started manually after a
#     reboot. The worker's run script waits for the endpoint and the worker
#     itself gates every claim, so a late model shows up as "not ready", not as
#     failed jobs.
#   * Termux:Boot must be installed and Termux launched once after installing
#     it; see device/termux/README.md.

set -u

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOME_DIR="${HOME:?HOME must be set}"
LOG_DIR="${PLATRIXA_LOG_DIR:-$HOME_DIR/.platrixa/logs}"
SERVICE_DIR="$PREFIX/var/service"

export PATH="$PREFIX/bin:$PATH"
mkdir -p "$LOG_DIR"

# Keep the CPU awake while the worker is supervised, when termux-api is present.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
fi

# Start runsvdir once. `pgrep -f` is used only for this supervisor check; the
# worker itself is never killed or matched by name anywhere.
if ! pgrep -f "runsvdir $SERVICE_DIR" >/dev/null 2>&1; then
    nohup runsvdir -P "$SERVICE_DIR" >>"$LOG_DIR/runsvdir.log" 2>&1 &
fi

# Give runit a moment to notice the service directory, then bring it up.
sleep 2
if command -v sv >/dev/null 2>&1; then
    sv up platrixa-worker >>"$LOG_DIR/runsvdir.log" 2>&1 || true
fi
