"""
Flask web dashboard for simulation monitoring.
Serves an interactive page with status cards, Plotly charts, GPU stats, and log tail.
"""

import json
from flask import Flask, render_template_string, jsonify, request
from poller import poll_all

app = Flask(__name__)

# Global state updated by the poller thread
_latest_data = None
_host = None
_simulations = None


def init_dashboard(host, simulations):
    global _host, _simulations
    _host = host
    _simulations = simulations


def update_data(data):
    global _latest_data
    _latest_data = data


def update_simulations(simulations):
    """Update the simulations list (called when auto-detected sims are found)."""
    global _simulations
    _simulations = simulations


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulation Monitor</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 16px; width: 100%; overflow-x: hidden; overflow-y: auto; }
  h1 { color: #58a6ff; margin-bottom: 20px; font-size: 24px; }
  .grid { display: grid; grid-template-columns: 1fr; gap: 12px; margin-bottom: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; overflow: hidden; }
  .card h2 { font-size: 16px; color: #58a6ff; margin-bottom: 12px; }
  .card h3 { font-size: 14px; color: #8b949e; margin-bottom: 8px; }

  .status-badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .status-running { background: #0d4429; color: #3fb950; border: 1px solid #238636; }
  .status-completed { background: #0c2d6b; color: #58a6ff; border: 1px solid #1f6feb; }
  .status-stopped { background: #3d1d00; color: #d29922; border: 1px solid #9e6a03; }
  .status-unreachable { background: #3d0000; color: #f85149; border: 1px solid #da3633; }
  .auto-badge { font-size: 10px; color: #8b949e; font-weight: 400; margin-left: 6px; }

  .progress-bar { background: #21262d; border-radius: 4px; height: 24px; margin: 8px 0; position: relative; overflow: hidden; }
  .progress-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease; }
  .progress-fill.running { background: linear-gradient(90deg, #238636, #3fb950); }
  .progress-fill.completed { background: linear-gradient(90deg, #1f6feb, #58a6ff); }
  .progress-fill.stopped { background: linear-gradient(90deg, #9e6a03, #d29922); }
  .progress-text { position: absolute; right: 8px; top: 3px; font-size: 12px; font-weight: 600; }

  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
  .stat { text-align: center; }
  .stat-value { font-size: 20px; font-weight: 700; color: #f0f6fc; }
  .stat-label { font-size: 11px; color: #8b949e; }

  .gpu-bar { background: #21262d; border-radius: 4px; height: 16px; margin: 4px 0; }
  .gpu-fill { height: 100%; border-radius: 4px; background: #da3633; transition: width 0.5s; }
  .gpu-row { display: flex; justify-content: space-between; align-items: center; margin: 6px 0; overflow: hidden; }

  .plot-container { margin-top: 12px; }

  .log-box { background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
             padding: 8px; font-family: 'SF Mono', 'Menlo', monospace; font-size: 11px;
             max-height: 150px; overflow-y: auto; overflow-x: hidden; word-break: break-all;
             line-height: 1.6; color: #8b949e; }

  .btn { padding: 6px 16px; border-radius: 6px; border: 1px solid #30363d;
         background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 13px; }
  .btn:hover { background: #30363d; }
  .btn-danger { border-color: #da3633; color: #f85149; }
  .btn-danger:hover { background: #da3633; color: #fff; }
  .btn:disabled { opacity: 0.5; cursor: wait; }
  .btn-quit { border-color: #da3633; color: #f85149; display: none; }
  .btn-quit:hover { background: #da3633; color: #fff; }

  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .last-update { color: #8b949e; font-size: 12px; }
  .controls { display: flex; gap: 8px; align-items: center; }
</style>
</head>
<body>

<div class="header">
  <h1>MD Simulation Monitor</h1>
  <div class="controls">
    <span class="last-update" id="lastUpdate"></span>
    <button class="btn" onclick="refresh()">Refresh</button>
    <button class="btn btn-quit" id="quitBtn" onclick="quitApp()">Quit</button>
  </div>
</div>

<div class="grid" id="simCards"></div>

<div class="grid">
  <div class="card" id="gpuCard">
    <h2>GPU</h2>
    <div id="gpuContent">Loading...</div>
  </div>
  <div class="card">
    <h2>Log Tail</h2>
    <select id="logSelect" onchange="updateLogTail()" style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:4px 8px;margin-bottom:8px;font-size:13px;"></select>
    <div class="log-box" id="logTail">Loading...</div>
  </div>
</div>

<div class="grid" id="plotCards"></div>

<script>
let data = null;

function statusClass(s) { return 'status-' + (s || 'stopped'); }

function renderSimCards(sims) {
  const container = document.getElementById('simCards');
  container.innerHTML = sims.map((s, i) => `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h2>${s.name}${s._auto_detected ? '<span class="auto-badge">(auto)</span>' : ''}</h2>
        <span class="status-badge ${statusClass(s.status)}">${s.status}</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill ${s.status}" style="width:${s.percent}%"></div>
        <span class="progress-text">${s.percent}%</span>
      </div>
      <div class="stats">
        <div class="stat">
          <div class="stat-value">${s.current_ns}/${s.target_ns}</div>
          <div class="stat-label">ns</div>
        </div>
        <div class="stat">
          <div class="stat-value">${s.speed || '—'}</div>
          <div class="stat-label">ns/day</div>
        </div>
        <div class="stat">
          <div class="stat-value">${s.eta_human || '—'}</div>
          <div class="stat-label">ETA</div>
        </div>
      </div>
      <div class="stats" style="margin-top:8px;">
        <div class="stat">
          <div class="stat-value" style="font-size:16px">${s.temperature ? s.temperature + ' K' : '—'}</div>
          <div class="stat-label">Temperature</div>
        </div>
        <div class="stat">
          <div class="stat-value" style="font-size:16px">${s.density || '—'}</div>
          <div class="stat-label">Density (g/mL)</div>
        </div>
        <div class="stat">
          <div class="stat-value" style="font-size:16px">${s.energy ? Math.round(s.energy).toLocaleString() : '—'}</div>
          <div class="stat-label">Total E (kJ/mol)</div>
        </div>
      </div>
      ${s.status === 'running' ? `<div style="margin-top:12px;text-align:right"><button class="btn btn-danger" onclick="stopSim('${s.name.replace(/'/g,"\\'")}')">Stop</button></div>` : ''}
      ${s.status === 'stopped' ? `<div style="margin-top:12px;text-align:right"><button class="btn" style="border-color:#3fb950;color:#3fb950" onclick="restartSim('${s.name.replace(/'/g,"\\'")}')">Restart</button></div>` : ''}
    </div>
  `).join('');
}

function renderGPU(gpu) {
  const el = document.getElementById('gpuContent');
  if (gpu.error) { el.innerHTML = `<span style="color:#f85149">${gpu.error}</span>`; return; }
  el.innerHTML = `
    <div style="font-size:13px;color:#8b949e;margin-bottom:8px">${gpu.name}</div>
    <div class="gpu-row">
      <span>GPU Util</span>
      <span style="font-weight:700">${gpu.gpu_util}%</span>
    </div>
    <div class="gpu-bar"><div class="gpu-fill" style="width:${gpu.gpu_util}%;background:${gpu.gpu_util>80?'#3fb950':gpu.gpu_util>40?'#d29922':'#8b949e'}"></div></div>
    <div class="gpu-row">
      <span>Memory</span>
      <span style="font-weight:700">${gpu.mem_used_mb} / ${gpu.mem_total_mb} MB (${gpu.mem_util}%)</span>
    </div>
    <div class="gpu-bar"><div class="gpu-fill" style="width:${gpu.mem_util}%;background:${gpu.mem_util>80?'#f85149':'#58a6ff'}"></div></div>
    <div class="gpu-row">
      <span>Temperature</span>
      <span style="font-weight:700;color:${gpu.temperature>80?'#f85149':gpu.temperature>60?'#d29922':'#3fb950'}">${gpu.temperature} C</span>
    </div>
  `;
}

function renderPlots(sims) {
  const container = document.getElementById('plotCards');
  container.innerHTML = '';
  const plotLayout = {
    paper_bgcolor: '#161b22', plot_bgcolor: '#0d1117',
    font: { color: '#8b949e', size: 11 },
    margin: { l: 50, r: 20, t: 30, b: 40 },
    xaxis: { gridcolor: '#21262d', title: 'Time (ns)', rangemode: 'tozero' },
    yaxis: { gridcolor: '#21262d' },
    legend: { bgcolor: 'rgba(0,0,0,0)', font: { size: 10 } },
    height: 250,
  };

  sims.forEach(s => {
    if (!s.log_data || s.log_data.length < 2) return;
    const t = s.log_data.map(d => d.time_ns);

    const card = document.createElement('div');
    card.className = 'card';
    card.style.gridColumn = '1 / -1';
    card.innerHTML = `<h2>${s.name} — Time Series</h2>
      <div class="plot-container" style="display:grid;grid-template-columns:1fr;gap:8px;">
        <div id="plot-energy-${s.name.replace(/\\W/g,'')}"></div>
        <div id="plot-temp-${s.name.replace(/\\W/g,'')}"></div>
        <div id="plot-density-${s.name.replace(/\\W/g,'')}"></div>
        <div id="plot-speed-${s.name.replace(/\\W/g,'')}"></div>
      </div>`;
    container.appendChild(card);

    const id = s.name.replace(/\\W/g, '');
    const simLayout = { ...plotLayout, xaxis: { ...plotLayout.xaxis, range: [0, s.target_ns] } };
    Plotly.newPlot('plot-energy-' + id, [
      { x: t, y: s.log_data.map(d => d.total_energy), type: 'scatter', mode: 'lines',
        line: { color: '#58a6ff', width: 1 }, name: 'Total Energy' },
    ], { ...simLayout, yaxis: { ...plotLayout.yaxis, title: 'kJ/mol' }, title: { text: 'Total Energy', font: { size: 13 } } }, { responsive: true });

    Plotly.newPlot('plot-temp-' + id, [
      { x: t, y: s.log_data.map(d => d.temperature), type: 'scatter', mode: 'lines',
        line: { color: '#f85149', width: 1 }, name: 'Temperature' },
    ], { ...simLayout, yaxis: { ...plotLayout.yaxis, title: 'K' }, title: { text: 'Temperature', font: { size: 13 } } }, { responsive: true });

    Plotly.newPlot('plot-density-' + id, [
      { x: t, y: s.log_data.map(d => d.density), type: 'scatter', mode: 'lines',
        line: { color: '#3fb950', width: 1 }, name: 'Density' },
    ], { ...simLayout, yaxis: { ...plotLayout.yaxis, title: 'g/mL' }, title: { text: 'Density', font: { size: 13 } } }, { responsive: true });

    Plotly.newPlot('plot-speed-' + id, [
      { x: t, y: s.log_data.map(d => d.speed_ns_day), type: 'scatter', mode: 'lines',
        line: { color: '#d29922', width: 1 }, name: 'Speed' },
    ], { ...simLayout, yaxis: { ...plotLayout.yaxis, title: 'ns/day' }, title: { text: 'Speed', font: { size: 13 } } }, { responsive: true });
  });
}

function updateLogTail() {
  if (!data) return;
  const sel = document.getElementById('logSelect');
  const idx = parseInt(sel.value);
  const sim = data.simulations[idx];
  const el = document.getElementById('logTail');
  if (sim && sim.log_tail && sim.log_tail.length > 0) {
    el.textContent = sim.log_tail.join('\\n');
    el.scrollTop = el.scrollHeight;
  } else {
    el.textContent = 'No log data available';
  }
}

function renderLogSelect(sims) {
  const sel = document.getElementById('logSelect');
  const prev = sel.value;
  sel.innerHTML = sims.map((s, i) => `<option value="${i}">${s.name}</option>`).join('');
  if (prev !== '') { sel.value = prev; }
  else {
    const runIdx = sims.findIndex(s => s.status === 'running');
    if (runIdx >= 0) sel.value = runIdx;
  }
}

function render(d) {
  data = d;
  // Sort: running first, then stopped, then completed — within each group, auto-detected (recent) before manual
  d.simulations.sort((a, b) => {
    const order = { running: 0, stopped: 1, completed: 2, unreachable: 3, error: 4 };
    const aOrd = order[a.status] ?? 5;
    const bOrd = order[b.status] ?? 5;
    if (aOrd !== bOrd) return aOrd - bOrd;
    // Auto-detected (recently active) sims sort above manual/old ones
    const aAuto = a._auto_detected ? 0 : 1;
    const bAuto = b._auto_detected ? 0 : 1;
    return aAuto - bAuto;
  });
  document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date(d.timestamp).toLocaleTimeString();
  renderSimCards(d.simulations);
  renderGPU(d.gpu);
  renderLogSelect(d.simulations);
  updateLogTail();
  renderPlots(d.simulations);
}

async function refresh() {
  try {
    const resp = await fetch('/api/status');
    const d = await resp.json();
    render(d);
  } catch (e) {
    document.getElementById('lastUpdate').textContent = 'Error: ' + e.message;
  }
}

// Popover detection — WKWebView doesn't support confirm()/alert()
const isPopover = new URLSearchParams(window.location.search).has('popover');
if (isPopover) {
  document.getElementById('quitBtn').style.display = 'inline-block';
}

async function stopSim(name) {
  if (!isPopover && !confirm('Stop this simulation?')) return;
  const btn = event.target;
  btn.textContent = 'Stopping...';
  btn.disabled = true;
  try {
    await fetch('/api/stop', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: name}) });
    setTimeout(async () => { await fetch('/api/refresh', {method:'POST'}); setTimeout(refresh, 3000); }, 2000);
  } catch (e) { btn.textContent = 'Error'; if (!isPopover) alert('Error: ' + e.message); }
}

async function restartSim(name) {
  if (!isPopover && !confirm('Restart this simulation?')) return;
  const btn = event.target;
  btn.textContent = 'Starting...';
  btn.disabled = true;
  try {
    await fetch('/api/restart', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: name}) });
    setTimeout(async () => { await fetch('/api/refresh', {method:'POST'}); setTimeout(refresh, 5000); }, 3000);
  } catch (e) { btn.textContent = 'Error'; if (!isPopover) alert('Error: ' + e.message); }
}

async function quitApp() {
  try {
    await fetch('/api/quit', { method: 'POST' });
  } catch (e) { /* app is terminating */ }
}

// Initial load + auto-refresh
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/status')
def api_status():
    global _latest_data, _host, _simulations
    # Do a fresh poll if no data yet or if requested
    if _latest_data is None and _host and _simulations:
        _latest_data = poll_all(_host, _simulations)
    if _latest_data is None:
        return jsonify({'error': 'No data yet', 'simulations': [], 'gpu': {}, 'timestamp': None})
    # Serialize log_data for JSON (it's already a list of dicts)
    return jsonify(_latest_data)


def _find_sim_by_name(name):
    """Look up a simulation config by name."""
    for s in (_simulations or []):
        if s.get('name') == name:
            return s
    return None


@app.route('/api/stop', methods=['POST'])
def api_stop():
    global _host, _simulations
    data = request.get_json()
    name = data.get('name', '')
    sim = _find_sim_by_name(name)
    if not sim:
        return jsonify({'error': f'Simulation not found: {name}'}), 400
    directory = sim.get('directory', '')
    if not directory:
        return jsonify({'error': 'No directory configured'}), 400

    from poller import ssh_run
    # Kill python processes whose cwd matches the sim directory + kill process group
    result = ssh_run(_host,
        f"for pid in $(pgrep '[p]ython' 2>/dev/null); do "
        f"cwd=$(readlink /proc/$pid/cwd 2>/dev/null); "
        f"[ \"$cwd\" = \"{directory}\" ] && kill -- -$(ps -o pgid= -p $pid | tr -d ' ') 2>/dev/null; "
        f"kill $pid 2>/dev/null; done")
    return jsonify({'ok': True, 'result': result})


@app.route('/api/restart', methods=['POST'])
def api_restart():
    """Restart a stopped simulation."""
    global _host, _simulations
    data = request.get_json()
    name = data.get('name', '')
    sim = _find_sim_by_name(name)
    if not sim:
        return jsonify({'error': f'Simulation not found: {name}'}), 400
    directory = sim.get('directory', '')
    script = sim.get('script', '')
    launch_cmd = sim.get('launch_cmd', '')
    if not launch_cmd:
        if script:
            launch_cmd = f"cd ~/code/md-learning && nohup conda run -n md-env python {script} > /dev/null 2>&1 &"
        else:
            return jsonify({'error': 'No launch command available — stop and restart manually'}), 400

    from poller import ssh_run
    # Source conda init for non-interactive SSH, then run the launch command
    conda_init = "source /home/max/miniforge3/etc/profile.d/conda.sh && conda activate md-env && "
    result = ssh_run(_host, conda_init + launch_cmd)
    return jsonify({'ok': True, 'result': result})


@app.route('/api/quit', methods=['POST'])
def api_quit():
    """Quit the application."""
    import os, threading
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify({'ok': True})


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    global _latest_data, _host, _simulations
    if _host and _simulations:
        _latest_data = poll_all(_host, _simulations)
    return jsonify({'ok': True})
