# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A macOS menu bar app that monitors molecular dynamics (MD) simulations running on a remote Linux host (`celeste`) via SSH polling. Uses **rumps** for the menu bar icon, **NSPopover + WKWebView** for a native dropdown dashboard, and **Flask** for the dashboard backend at `localhost:5050`.

## Running

```bash
source ~/sim-monitor-env/bin/activate
python monitor.py
```

The venv is at `~/sim-monitor-env` (Python 3.12, Homebrew). All deps are in `requirements.txt` (rumps, flask, plotly, pyyaml, pyobjc-framework-WebKit).

## Syncing from Remote

Source of truth lives on celeste at `~/code/md-learning/sim-monitor/`. To pull latest:
```bash
rsync -avz celeste:~/code/md-learning/sim-monitor/ ~/code/sim-monitor/
```

## Architecture

**monitor.py** — Entry point. `SimMonitorApp(rumps.App)` starts Flask in a daemon thread and kicks off SSH polling in another. A `queue.Queue` bridges background poll results to the main thread, where a `@rumps.timer(2)` callback applies UI updates (macOS requires all UI on the main thread).

**poller.py** — `poll_all()` SSHs into `celeste` via `subprocess.run(['ssh', ...])`. For each simulation it runs a single compound SSH command (separated by `===MARKER===` lines) that grabs the log tail, history, process status. `poll_gpu()` runs `nvidia-smi`. Parses CSV-format `production.log` files (step, time_ps, energies, temperature, volume, density, speed).

**popover.py** — Native macOS popover using PyObjC. `WebViewController(NSViewController)` hosts a WKWebView loading the Flask dashboard. `PopoverClickHandler(NSObject)` toggles the popover on status bar button click. `setup_popover()` wires everything up after rumps initializes.

**dashboard.py** — Flask app with a single-page inline HTML template (no separate files). Routes: `/` (dashboard), `/api/status` (JSON), `/api/stop` (POST, kills sim via `pkill`), `/api/restart` (POST, launches sim script on celeste), `/api/quit` (POST, terminates the app), `/api/refresh` (POST). Dashboard auto-refreshes every 30s via JS `setInterval`. Global `_latest_data` is updated by the poller thread.

**config.yaml** — Defines simulations (name, remote directory, script name, log file, target_ns). Simulations can be marked `status: completed` to skip polling. Optional `launch_cmd` per simulation overrides the default restart command (`cd ~/code/md-learning && nohup conda run -n md-env python {script} > /dev/null 2>&1 &`). Script paths in config are relative to `~/code/md-learning/`.

## Menu Bar Title Length

On MacBooks with a notch, macOS hides status bar items that are too wide. Titles must be kept very short (~5 chars max) or the item gets pushed behind the notch and becomes invisible. This is why the title shows just the percentage (e.g., "87%") rather than "MD: Tet5-VC 87%". Full simulation details are in the dropdown menu items instead.

## Key Details

- SSH requires key auth to `celeste` (already configured)
- `debug.log` is written by the poller for troubleshooting SSH issues — not committed, can grow large
- No tests exist in this project
- The dashboard HTML/CSS/JS is all inline in `DASHBOARD_HTML` string in dashboard.py
- WSL nvidia-smi path is tried first as a fallback (`/usr/lib/wsl/lib/nvidia-smi`)
