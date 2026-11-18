# Platrixa — Termux worker supervision (S2)

This directory contains the reproducible, device-side service definition for the
outbound inference worker. Nothing here runs on Railway, and nothing here starts
the Android model app.

```
device/termux/
  boot/platrixa-worker-boot.sh          → ~/.termux/boot/  (Termux:Boot)
  service/platrixa-worker/run           → $PREFIX/var/service/platrixa-worker/
  service/platrixa-worker/finish        → $PREFIX/var/service/platrixa-worker/
  worker.env.example                    → ~/.platrixa/worker.env
  install-termux-service.sh             installs the three assets above
```

## What this does and does not do

| | |
|---|---|
| Supervises the worker with runit | **yes** |
| Refuses to start on bad config / insecure key file | **yes** |
| Waits for `http://127.0.0.1:11434/v1/models` before starting | **yes**, bounded |
| Prevents a duplicate worker (PID-aware lock, stale-lock recovery) | **yes** |
| Bounded restart backoff + log rotation | **yes** (`finish`) |
| Starts the Android model app | **no** — it has no documented boot receiver |
| Starts Termux itself | **no** — Termux:Boot starts Termux after unlock |
| Contains or prints the API key | **never** — the key lives in `~/.platrixa/worker.key` |

The worker additionally re-checks model readiness before **every** claim (S1),
so even a model that dies after the service started cannot burn a job's retry
budget: the worker simply stops claiming until the endpoint answers again.

## One-time device setup

Run these **inside Termux** on the phone:

```sh
pkg update
pkg install python curl runit termux-api
# Termux:Boot must also be installed from F-Droid and opened once.

# 1. Clone the repository (any private location is fine).
cd "$HOME"
git clone https://github.com/pawarpranay2010-sketch/financial-timeline-engine-v0.git platrixa
cd platrixa

# 2. Put the tenant key in the protected file (paste it at the prompt; it is
#    never echoed into shell history by this form).
mkdir -p "$HOME/.platrixa"
read -r -s -p "tenant API key: " KEY; printf '%s' "$KEY" > "$HOME/.platrixa/worker.key"; unset KEY
chmod 600 "$HOME/.platrixa/worker.key"

# 3. Configure the service environment.
cp device/termux/worker.env.example "$HOME/.platrixa/worker.env"
chmod 600 "$HOME/.platrixa/worker.env"
nano "$HOME/.platrixa/worker.env"     # set PLATRIXA_REPO_DIR to this clone

# 4. Install the runit service and the boot hook (does NOT start anything).
sh device/termux/install-termux-service.sh
```

Confirm the model server is up **before** starting the worker:

```sh
curl -s http://127.0.0.1:11434/v1/models
# must list the id you set as PLATRIXA_WORKER_MODEL_ID (e.g. "default")
```

## Start / stop / status

```sh
sv up platrixa-worker        # start (or resume)
sv status platrixa-worker    # run / down / restarts
sv down platrixa-worker      # stop
sv restart platrixa-worker   # after editing worker.env
tail -f ~/.platrixa/logs/worker.log
```

On the very first run the worker may sit on:

```
model endpoint not ready at http://127.0.0.1:11434/v1/models (attempt 1/60); waiting 2s
```

until the Android model app is started and has loaded the GGUF. That is
expected; it is not a crash.

## Recovery procedures

| Symptom | Action |
|---|---|
| `sv status` shows `down` | `sv up platrixa-worker`; read the log first |
| Log shows `missing env file` / `missing API key file` | restore the file, `chmod 600`, then `sv restart platrixa-worker` |
| Log shows `refusing a key file with permissions` | `chmod 600 ~/.platrixa/worker.key` |
| Log shows `model endpoint never became ready` | start the Android model app and load the model; the next restart attempt picks it up |
| Worker claims but every model call 503s | the app's single inference slot is busy/loading; wait for `/v1/models` and check the app UI |
| Two workers suspected | the run script refuses the duplicate and logs `not starting a duplicate`; check `sv status` and `ps -A -o pid,args | grep -i platrixa` |
| After a reboot, no worker | (1) unlock the phone once, (2) check Termux:Boot is installed, (3) start the model app, (4) `sv up platrixa-worker` |

## Uninstall

```sh
sv down platrixa-worker
rm -rf "$PREFIX/var/service/platrixa-worker"
rm -f "$HOME/.termux/boot/platrixa-worker-boot.sh"
# keep ~/.platrixa/worker.key unless you intend to revoke the key server-side
```

## Verified vs unverified

Verified locally (this repository, no device): shell syntax, env validation,
key-permission refusal, readiness wait/recovery, print-command contract, secret
redaction, duplicate refusal, stale-lock recovery, finish backoff, log rotation.

**Not verified here:** actual runit behaviour on the phone, Termux:Boot timing,
the Android model app's own lifecycle, and a real screen-off soak. Those require
the device and are listed as device-side checks in the final report.
