#!/data/data/com.termux/files/usr/bin/sh
# Platrixa — install ONE named Termux worker instance (S2 + M7).
#
# Run INSIDE Termux from a clone of the repository:
#
#   sh device/termux/install-termux-service.sh --instance alice          # prepare
#   sh device/termux/install-termux-service.sh --instance alice --start  # prepare + start
#
# Each instance gets its own service directory, env file, key file, log,
# status file and lock. Several instances run under ONE runsvdir.
#
# It writes:
#   $PREFIX/var/service/platrixa-worker[-INSTANCE]/{run,finish}
#   ~/.termux/boot/platrixa-worker-boot.sh        (shared, brings up all)
#   ~/.platrixa/worker[-INSTANCE].env             (from the example, only if absent)
#   ~/.platrixa/logs/, ~/.platrixa/status/, ~/.platrixa/slots/
#
# It never writes, prints, or asks for the API key. The key file
# (~/.platrixa/worker[-INSTANCE].key) must already exist with mode 600.

set -eu

INSTANCE="default"
START=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --instance)
            [ "$#" -ge 2 ] || { echo "--instance needs a name" >&2; exit 2; }
            INSTANCE="$2"; shift 2 ;;
        --instance=*) INSTANCE="${1#--instance=}"; shift ;;
        --start) START=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

case "$INSTANCE" in
    default|'' ) INSTANCE="default"; SUFFIX="" ;;
    *[!A-Za-z0-9_-]* ) echo "invalid instance name '$INSTANCE' (use A-Z a-z 0-9 _ -)" >&2; exit 2 ;;
    * ) SUFFIX="-$INSTANCE" ;;
esac

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
SERVICE_NAME="platrixa-worker$SUFFIX"
SERVICE_DIR="$PREFIX/var/service/$SERVICE_NAME"
BOOT_DIR="$HOME_DIR/.termux/boot"
PRIVATE_DIR="$HOME_DIR/.platrixa"

say() { echo "platrixa-install[$INSTANCE]: $*"; }

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
run mkdir -p "$SERVICE_DIR" "$BOOT_DIR" "$PRIVATE_DIR/logs" "$PRIVATE_DIR/status" "$PRIVATE_DIR/slots" "$PRIVATE_DIR/locks"
run cp "$SERVICE_SRC/run" "$SERVICE_DIR/run"
run cp "$SERVICE_SRC/finish" "$SERVICE_DIR/finish"
run chmod 755 "$SERVICE_DIR/run" "$SERVICE_DIR/finish"
run cp "$BOOT_SRC" "$BOOT_DIR/platrixa-worker-boot.sh"
run chmod 755 "$BOOT_DIR/platrixa-worker-boot.sh"

ENV_FILE="$PRIVATE_DIR/worker$SUFFIX.env"
if [ ! -f "$ENV_FILE" ]; then
    run cp "$ENV_EXAMPLE" "$ENV_FILE"
    run chmod 600 "$ENV_FILE"
    say "created $ENV_FILE — EDIT IT (repo path, worker id, model id, this instance's tenant key)"
else
    say "kept existing $ENV_FILE"
fi

# --- credential checks (never read the value) ------------------------------
KEY_FILE="$PRIVATE_DIR/worker$SUFFIX.key"
if [ ! -f "$KEY_FILE" ]; then
    say "MISSING $KEY_FILE — create it with THIS instance's tenant key, then: chmod 600 $KEY_FILE"
    say "the service will refuse to start until it exists"
else
    perms="$(stat -c '%a' "$KEY_FILE" 2>/dev/null || echo '')"
    case "$perms" in
        600|400) say "key file present with safe permissions ($perms)" ;;
        *) say "UNSAFE permissions on $KEY_FILE ($perms) — run: chmod 600 $KEY_FILE" ;;
    esac
fi

# --- a second instance must not share another instance's credential --------
# Compared by PATH only; no key material is ever read or compared.
if [ "$INSTANCE" != "default" ] && [ -f "$KEY_FILE" ] && [ -f "$PRIVATE_DIR/worker.key" ]; then
    if cmp -s "$KEY_FILE" "$PRIVATE_DIR/worker.key"; then
        say "REFUSING: $KEY_FILE is identical to the legacy default key file."
        say "each worker instance must use its own tenant credential."
        exit 3
    fi
fi

say "assets installed for service '$SERVICE_NAME'."
say "The Android model app is NOT started by this installer, by Termux:Boot, or by the worker."
say "logs: $PRIVATE_DIR/logs/worker$SUFFIX.log"
say "status: $PRIVATE_DIR/status/worker$SUFFIX.json"

if [ "$START" = "1" ]; then
    run sv up "$SERVICE_NAME"
    say "requested: sv up $SERVICE_NAME"
else
    say "not started. To start now: sv up $SERVICE_NAME"
    say "                       (or: sv restart $SERVICE_NAME after edits)"
fi
