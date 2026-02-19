#!/usr/bin/env python3
"""
Simulation Monitor — macOS Menu Bar App
========================================
Uses rumps for the menu bar, Flask + NSPopover for the dashboard.
Click the menu bar icon to see the dashboard in a native popover.

Usage:
    pip install rumps flask plotly pyyaml pyobjc-framework-WebKit
    python monitor.py
"""

import os
import sys
import threading
import queue
import logging

import rumps
import yaml

logging.getLogger('werkzeug').setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from poller import poll_all
from dashboard import app as flask_app, init_dashboard, update_data
from popover import setup_popover


def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(config_path) as f:
        return yaml.safe_load(f)


class SimMonitorApp(rumps.App):
    def __init__(self, config):
        self.config = config
        self.host = config['host']
        self.simulations = config['simulations']
        self.poll_interval = config.get('poll_interval', 30)
        self.port = config.get('dashboard_port', 5050)
        self.latest_data = None
        self._update_queue = queue.Queue()
        self._poll_count = 0
        self._polling = False
        self._popover_ready = False

        super().__init__(
            name="SimMonitor",
            title="MD",
            quit_button=None,
        )

        # Start Flask in background
        init_dashboard(self.host, self.simulations)
        threading.Thread(target=self._run_flask, daemon=True).start()

        # Kick off initial poll immediately
        threading.Thread(target=self._do_poll, daemon=True).start()

    def _run_flask(self):
        import logging as lg
        lg.getLogger('werkzeug').disabled = True
        flask_app.run(host='127.0.0.1', port=self.port, debug=False, use_reloader=False)

    @rumps.timer(2)
    def check_updates(self, _):
        """Runs on the MAIN thread every 2s — safe to update UI here."""
        # One-time popover setup after rumps initializes the status bar
        if not self._popover_ready:
            try:
                setup_popover(self._nsapp.nsstatusitem, self.port)
                self._popover_ready = True
            except AttributeError:
                pass

        try:
            data = self._update_queue.get_nowait()
            self._apply_data(data)
        except queue.Empty:
            pass

        # Trigger a new poll every poll_interval seconds (poll_interval / 2s ticks)
        self._poll_count += 1
        if self._poll_count >= self.poll_interval // 2 and not self._polling:
            self._poll_count = 0
            threading.Thread(target=self._do_poll, daemon=True).start()

    def _do_poll(self):
        """Runs in background thread — NO UI updates here."""
        self._polling = True
        try:
            data = poll_all(self.host, self.simulations)
            self.latest_data = data
            update_data(data)
            self._update_queue.put(data)
        except Exception:
            self._update_queue.put(None)
        finally:
            self._polling = False

    def _apply_data(self, data):
        """Called on the MAIN thread only — safe to update menu bar."""
        if data is None:
            self.title = "err"
            return

        # Update title (keep short — long titles get hidden behind MacBook notch)
        running = [s for s in data['simulations'] if s.get('status') == 'running']
        if running:
            focus = min(running, key=lambda s: s.get('percent', 0))
            self.title = f"MD {focus.get('percent', 0):.0f}%"
        else:
            completed = [s for s in data['simulations'] if s.get('status') == 'completed']
            if len(completed) == len(data['simulations']):
                self.title = "done"
            else:
                self.title = "idle"


if __name__ == '__main__':
    import socket
    config = load_config()
    port = config.get('dashboard_port', 5050)

    # Prevent duplicate instances — check if port is already in use
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', port))
        sock.close()
    except OSError:
        print(f"SimMonitor already running (port {port} in use)")
        sys.exit(0)

    app = SimMonitorApp(config)
    app.run()
