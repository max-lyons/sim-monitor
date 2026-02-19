# Sim-Monitor Handoff

## What this is
A macOS menu bar app (rumps + Flask) that monitors MD simulations running on a remote Linux box (`celeste`) via SSH polling. Menu bar shows progress; "Open Dashboard" opens a Plotly web dashboard at localhost:5050.

## Files
- `monitor.py` — Main entry point. rumps menu bar app + Flask thread.
- `poller.py` — SSH subprocess calls to celeste, parses production.log CSV, nvidia-smi.
- `dashboard.py` — Flask app serving a single-page Plotly dashboard.
- `config.yaml` — Simulation definitions (host, log paths, target ns).
- `requirements.txt` — rumps, flask, plotly, pyyaml.

## Environment
- venv at `~/sim-monitor-env` (Python 3.12 via Homebrew)
- Activate: `source ~/sim-monitor-env/bin/activate`
- SSH to celeste works (key auth set up, tested)
- All deps installed in the venv
- Source code lives on celeste at `~/code/md-learning/sim-monitor/`

## The problem
The menu bar item appears briefly ("MD: ...") then **disappears after ~1 second**. The process continues running in the background — SSH polling works, debug.log gets written with correct data, Flask serves. But the NSStatusItem vanishes.

## What works
- SSH polling: connects to celeste, parses production.log, gets GPU stats — all correct
- Flask dashboard: starts on port 5050, serves data via /api/status
- Data parsing: simulation at ~411/500 ns, speed 328 ns/day, process detected

## What we've tried
1. **Pure PyObjC (NSPopover + WKWebView)** — crashed repeatedly, event loop issues
2. **rumps with UI updates from background thread** — menu bar disappears (thread-safety)
3. **rumps with queue-based main-thread UI updates** (current version) — not yet tested

## Most likely cause
macOS requires all UI updates on the main thread. Earlier versions called `self.title = ...` from a background `threading.Thread`. The current version uses a `queue.Queue` + `@rumps.timer(2)` to dispatch UI updates to the main thread. This might fix it, or the issue might be deeper (framework Python, app activation policy, etc.).

## To test
```bash
source ~/sim-monitor-env/bin/activate
cd ~/code/sim-monitor && python monitor.py
```

If the menu bar still disappears, things to investigate:
- Whether a minimal rumps app works at all (`rumps.App("Test", title="Hi").run()`)
- Whether the venv Python is a framework build (`python -c "import sys; print(sys.prefix)"`)
- NSApplication activation policy (may need `LSUIElement` or `NSApplicationActivationPolicyAccessory`)
- Whether `pythonw` or the framework binary at `/opt/homebrew/Frameworks/Python.framework/Versions/3.12/bin/python3.12` behaves differently

## To sync latest code from celeste
```bash
rsync -avz celeste:~/code/md-learning/sim-monitor/ ~/code/sim-monitor/
```
