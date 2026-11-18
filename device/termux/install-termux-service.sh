#!/data/data/com.termux/files/usr/bin/sh
# Platrixa — install the Termux worker supervision assets (S2).
#
# Run INSIDE Termux from a clone of the repository:
#
#   sh device/termux/install-termux-service.sh            # prepare only
#   sh device/termux/install-termux-service.sh --start    # prepare and start
#
# It writes:
#   $PREFIX/var/service/platrixa-worker/{run,finish}
#   ~/.termux/boot/platrixa-worker-boot.sh
#   ~/.platrixa/worker.env                (from the example, only if absent)
#   ~/.platrixa/logs/
#
# It never writes, prints, or asks for the API key. The key file
# (~/.platrixa/worker.key) must already exist with mode 600.

set -eu

START=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --start) START=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
case "$PREFIX" in
    *com.termux*) : ;;
    *)
        echo "this installer must run inside Termux (PREFIX=$PREFIX)" >&2
        exit 2
        ;;
esac

HOME_DIR="${HOME:?HOME must be set}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="$SOURCE_DIR/service/platrixa-worker"
BOOT_SRC="$SOURCE_DIR/boot/platrixa-worker-boot.sh"
ENV_EXAMPLE="$SOURCE_DIR/worker.env.example"
SERVICE_DIR="$PREFIX/var/service/platrixa-worker"
BOOT_DIR="$HOME_DIR/.termux/boot"
PRIVATE_DIR="$HOME_DIR/.platrixa"

say() { echo "platrixa-install: $*"; }

run() {
    if [ "$DRY_RUN" = "1" ]; then
        say "would run: $*"
    else
        "$@"
    fi
}

# --- prerequisites ---------------------------------------------------------
missing=0
for tool in curl python sv runsvdir; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        say "missing '$tool'; install with: pkg install $tool"
        missing=1
    fi
done
if [ "$missing" = "1" ]; then
    say "install the missing tools and re-run"
    exit 2
fi
if ! command -v termux-wake-lock >/dev/null 2>&1; then
    say "note: termux-wake-lock not found (optional; pkg install termux-api)"
fi

# --- assets ----------------------------------------------------------------
run mkdir -p "$SERVICE_DIR" "$BOOT_DIR" "$PRIVATE_DIR/logs"
run cp "$SERVICE_SRC/run" "$SERVICE_DIR/run"
run cp "$SERVICE_SRC/finish" "$SERVICE_DIR/finish"
run chmod 755 "$SERVICE_DIR/run" "$SERVICE_DIR/finish"
run cp "$BOOT_SRC" "$BOOT_DIR/platrixa-worker-boot.sh"
run chmod 755 "$BOOT_DIR/platrixa-worker-boot.sh"

if [ ! -f "$PRIVATE_DIR/worker.env" ]; then
    run cp "$ENV_EXAMPLE" "$PRIVATE_DIR/worker.env"
    run chmod 600 "$PRIVATE_DIR/worker.env"
    say "created $PRIVATE_DIR/worker.env — EDIT IT (repo path, worker id, model id)"
else
    say "kept existing $PRIVATE_DIR/worker.env"
fi

# --- credential checks (never read the value) ------------------------------
KEY_FILE="$PRIVATE_DIR/worker.key"
if [ ! -f "$KEY_FILE" ]; then
    say "MISSING $KEY_FILE — create it with the tenant key, then: chmod 600 $KEY_FILE"
    say "the service will refuse to start until it exists"
else
    perms="$(stat -c '%a' "$KEY_FILE" 2>/dev/null || echo '')"
    case "$perms" in
        600|400) say "key file present with safe permissions ($perms)" ;;
        *) say "UNSAFE permissions on $KEY_FILE ($perms) — run: chmod 600 $KEY_FILE" ;;
    esac
fi

say "assets installed. The Android model app is NOT started by this installer,"
say "by Termux:Boot, or by the worker: start the model server manually after a boot."
say "logs: $PRIVATE_DIR/logs/worker.log"

if [ "$START" = "1" ]; then
    run sv up platrixa-worker
    say "requested: sv up platrixa-worker"
else
    say "not started. To start now: sv up platrixa-worker"
    say "                       (or: sv restart platrixa-worker after edits)"
fi
