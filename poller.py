"""
SSH poller for remote simulation monitoring.
Connects to the remote host via subprocess ssh, collects simulation
status, log data, process info, and GPU stats in a single SSH call.
"""

import csv
import io
import json
import os
import subprocess
import time
from datetime import datetime, timedelta


# Cache of last successful poll result per simulation name.
# Used to preserve "completed" state when SSH becomes unreachable.
_last_known = {}

# Cache of previously discovered sim configs (keyed by normalized directory).
# Keeps sims visible after their process stops or log ages past the -mmin window.
_discovered_cache = {}


def ssh_run(host, command, timeout=15):
    """Run a command on the remote host via SSH."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10', host, command],
            capture_output=True, text=True, timeout=timeout
        )
        # Log errors for debugging
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug.log')
        if result.returncode != 0 or not result.stdout.strip():
            with open(debug_path, 'a') as f:
                f.write(f"ssh_run returncode={result.returncode}\n")
                f.write(f"ssh_run stderr={result.stderr[:300]}\n")
                f.write(f"ssh_run stdout={result.stdout[:300]}\n")
        # Return stdout even if exit code is non-zero (e.g. pgrep finds nothing)
        if result.stdout.strip():
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug.log')
        with open(debug_path, 'a') as f:
            f.write(f"ssh_run exception: {e}\n")
        return None


def parse_log_line(line):
    """Parse a single CSV line from a production.log file."""
    try:
        reader = csv.reader(io.StringIO(line))
        row = next(reader)
        if len(row) < 9:
            return None
        return {
            'step': int(row[0]),
            'time_ps': float(row[1]),
            'time_ns': float(row[1]) / 1000.0,
            'potential_energy': float(row[2]),
            'kinetic_energy': float(row[3]),
            'total_energy': float(row[4]),
            'temperature': float(row[5]),
            'volume': float(row[6]),
            'density': float(row[7]),
            'speed_ns_day': float(row[8]),
        }
    except (ValueError, StopIteration):
        return None


def parse_log_lines(text):
    """Parse multiple CSV lines from production.log, skipping the header."""
    results = []
    for line in text.strip().split('\n'):
        if line.startswith('#') or not line.strip():
            continue
        parsed = parse_log_line(line)
        if parsed:
            results.append(parsed)
    return results


def poll_simulation(host, sim_config):
    """Poll a single simulation's status."""
    name = sim_config['name']
    directory = sim_config['directory']
    log_file = sim_config.get('log', 'production.log')
    target_ns = sim_config.get('target_ns', 500)
    log_path = f"{directory}/{log_file}"
    script_name = sim_config.get('script', '')

    # If marked as completed in config, return static status
    if sim_config.get('status') == 'completed':
        return {
            'name': name,
            'status': 'completed',
            'current_ns': target_ns,
            'target_ns': target_ns,
            'percent': 100.0,
            'eta': None,
            'speed': None,
            'last_update': None,
            'log_data': [],
            'log_tail': [],
            'process_running': False,
        }

    # Build a single SSH command that gathers everything
    # Use markers to separate outputs
    if script_name and not sim_config.get('_auto_detected'):
        # Use [c]haracter class trick so pgrep doesn't match its own bash -c command
        escaped = '[' + script_name[0] + ']' + script_name[1:]
        process_cmd = f"pgrep -af '{escaped}' 2>/dev/null || echo 'NOT_RUNNING'"
    else:
        # For auto-detected sims, find python processes whose cwd matches the sim directory
        process_cmd = (
            f"found=0; for pid in $(pgrep '[p]ython' 2>/dev/null); do "
            f"cwd=$(readlink /proc/$pid/cwd 2>/dev/null); "
            f"if [ \"$cwd\" = \"{directory}\" ]; then "
            f"cmd=$(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null); "
            f"echo \"$pid $cmd\"; found=1; fi; done; "
            f"[ $found -eq 0 ] && echo 'NOT_RUNNING'"
        )
    commands = [
        f"echo '===TAIL==='",
        f"tail -1 {log_path} 2>/dev/null || echo 'NO_LOG'",
        f"echo '===HISTORY==='",
        f"cat {log_path} 2>/dev/null || echo 'NO_LOG'",
        f"echo '===PROCESS==='",
        process_cmd,
        f"echo '===LOGTAIL==='",
        f"tail -30 {log_path} 2>/dev/null || echo 'NO_LOG'",
    ]
    combined = ' ; '.join(commands)
    output = ssh_run(host, combined)

    # Debug: write raw SSH output to a log file
    debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug.log')
    with open(debug_path, 'a') as f:
        f.write(f"\n--- {datetime.now()} poll {name} ---\n")
        f.write(f"command: {combined[:200]}...\n")
        f.write(f"output: {repr(output[:500]) if output else 'None'}\n")

    if output is None:
        cached = _last_known.get(name)
        if cached:
            # Preserve last known data — progress can only go forward.
            # Keep completed status as-is; mark others as unreachable.
            if cached.get('status') != 'completed':
                cached = dict(cached, status='unreachable', error='SSH connection failed')
            return cached
        return {
            'name': name,
            'status': 'unreachable',
            'current_ns': 0,
            'target_ns': target_ns,
            'percent': 0,
            'eta': None,
            'speed': None,
            'last_update': None,
            'log_data': [],
            'log_tail': [],
            'process_running': False,
            'error': 'SSH connection failed',
        }

    # Parse sections
    sections = {}
    current_section = None
    current_lines = []
    for line in output.split('\n'):
        if line.startswith('===') and line.endswith('==='):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = line.strip('=')
            current_lines = []
        else:
            current_lines.append(line)
    if current_section:
        sections[current_section] = '\n'.join(current_lines)

    # Parse last log line for current status
    tail_text = sections.get('TAIL', '').strip()
    last_entry = parse_log_line(tail_text) if tail_text and tail_text != 'NO_LOG' else None

    # Debug: log what we parsed
    with open(debug_path, 'a') as f:
        f.write(f"sections found: {list(sections.keys())}\n")
        f.write(f"tail_text: {repr(tail_text[:200])}\n")
        f.write(f"last_entry: {last_entry}\n")
        f.write(f"process_text: {repr(sections.get('PROCESS', '')[:100])}\n")

    # Parse log history for plots
    history_text = sections.get('HISTORY', '')
    log_data = parse_log_lines(history_text) if history_text != 'NO_LOG' else []

    # Check process and capture cmdline for restart
    process_text = sections.get('PROCESS', '')
    process_running = process_text.strip() != 'NOT_RUNNING' and process_text.strip() != ''
    # Try to extract launch command from running process
    process_launch_cmd = ''
    if process_running and not script_name:
        for pline in process_text.strip().split('\n'):
            pline = pline.strip()
            if not pline or pline == 'NOT_RUNNING':
                continue
            # Format: "PID cmdline..."
            pparts = pline.split(None, 1)
            if len(pparts) >= 2 and pparts[0].isdigit():
                process_launch_cmd = f"cd {directory} && nohup conda run --no-capture-output -n md-env {pparts[1]} > /dev/null 2>&1 &"
                break

    # Log tail for display
    log_tail_text = sections.get('LOGTAIL', '')
    log_tail = []
    if log_tail_text and log_tail_text != 'NO_LOG':
        log_tail = [l for l in log_tail_text.split('\n') if l.strip()]

    # Calculate progress and ETA
    # Subtract equilibration time — first log entry marks the start of production
    first_ns = log_data[0]['time_ns'] if log_data else 0
    for entry in log_data:
        entry['time_ns'] -= first_ns
    current_ns = last_entry['time_ns'] if last_entry else 0
    production_ns = current_ns - first_ns
    percent = min(100.0, (production_ns / target_ns) * 100) if target_ns > 0 else 0
    speed = last_entry['speed_ns_day'] if last_entry else None

    eta = None
    if speed and speed > 0 and production_ns < target_ns:
        remaining_ns = target_ns - production_ns
        remaining_days = remaining_ns / speed
        eta = datetime.now() + timedelta(days=remaining_days)

    # Determine status based on progress and process detection
    # Use 99.5% threshold to handle floating point accumulation from timestep rounding
    if production_ns >= target_ns * 0.995:
        status = 'completed'
    elif process_running:
        status = 'running'
    else:
        status = 'stopped'

    result = {
        'name': name,
        'status': status,
        'current_ns': round(production_ns, 1),
        'target_ns': target_ns,
        'percent': round(percent, 1),
        'eta': eta.isoformat() if eta else None,
        'eta_human': eta.strftime('%b %d %H:%M') if eta else None,
        'speed': round(speed, 1) if speed else None,
        'temperature': round(last_entry['temperature'], 1) if last_entry else None,
        'density': round(last_entry['density'], 4) if last_entry else None,
        'energy': round(last_entry['total_energy'], 0) if last_entry else None,
        'last_update': datetime.now().isoformat(),
        'log_data': log_data,
        'log_tail': log_tail,
        'process_running': process_running,
        '_directory': directory,
        '_script': script_name,
        '_launch_cmd': sim_config.get('launch_cmd', '') or process_launch_cmd,
    }

    # Cache every successful poll so data survives SSH outages
    _last_known[name] = result

    return result


def discover_simulations(host):
    """Auto-detect simulations via GPU process scanning and active log files."""
    command = (
        "echo '===GPU===' ; "
        "nvidia-smi pmon -c 1 -s u 2>/dev/null | awk '/python/ {print $2}' | "
        "while read pid; do "
        "cwd=$(readlink /proc/$pid/cwd 2>/dev/null) ; "
        "cmd=$(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null) ; "
        "[ -n \"$cwd\" ] && echo \"$pid:$cwd:$cmd\" ; "
        "done ; "
        "echo '===RECENT===' ; "
        "find ~/code/md-learning/simulations ~/code/md/simulations "
        "-maxdepth 2 -name 'production.log' -type f -mmin -60 2>/dev/null ; "
        "echo '===META===' ; "
        "find ~/code/md-learning/simulations ~/code/md/simulations "
        "-maxdepth 2 -name 'sim_meta.json' -type f 2>/dev/null | "
        "while read f; do echo \"$f:\"; cat \"$f\"; done"
    )
    output = ssh_run(host, command, timeout=20)
    if output is None:
        # SSH failed — return cached discoveries so sims don't disappear
        return list(_discovered_cache.values())

    # Parse sections
    sections = {}
    current_section = None
    current_lines = []
    for line in output.split('\n'):
        if line.startswith('===') and line.endswith('==='):
            if current_section:
                sections[current_section] = current_lines
            current_section = line.strip('=')
            current_lines = []
        else:
            if line.strip():
                current_lines.append(line.strip())
    if current_section:
        sections[current_section] = current_lines

    discovered = {}

    # Parse GPU process results — each line is pid:cwd:cmdline
    for line in sections.get('GPU', []):
        parts = line.split(':', 2)
        if len(parts) < 2:
            continue
        cwd = parts[1].rstrip('/')
        cmdline = parts[2].strip() if len(parts) > 2 else ''
        # Only add if it's under a simulations directory
        if '/simulations/' not in cwd:
            continue
        # Build launch command from the captured cmdline, wrapping in conda env
        launch_cmd = ''
        if cmdline:
            # cmdline is the raw process args (e.g. "python scripts/foo.py")
            # Wrap in conda run to ensure correct environment
            launch_cmd = f"cd {cwd} && nohup conda run --no-capture-output -n md-env {cmdline} > /dev/null 2>&1 &"
        if cwd in discovered:
            # Update existing entry with launch_cmd if we got one from GPU
            if launch_cmd:
                discovered[cwd]['launch_cmd'] = launch_cmd
            continue
        name = os.path.basename(cwd)
        entry = {
            'name': name,
            'directory': cwd,
            'log': 'production.log',
            'target_ns': 500,
            'script': '',
            '_auto_detected': True,
        }
        if launch_cmd:
            entry['launch_cmd'] = launch_cmd
        discovered[cwd] = entry

    # Parse recently-modified logs (last 60 min) — catches active sims
    for log_path in sections.get('RECENT', []):
        sim_dir = os.path.dirname(log_path)
        norm_dir = sim_dir.rstrip('/')
        if norm_dir in discovered:
            continue
        name = os.path.basename(sim_dir)
        discovered[norm_dir] = {
            'name': name,
            'directory': sim_dir,
            'log': 'production.log',
            'target_ns': 500,
            'script': '',
            '_auto_detected': True,
        }

    # Parse sim_meta.json files to get target_ns
    meta_by_dir = {}
    current_meta_path = None
    meta_text = '\n'.join(sections.get('META', []))
    for block in meta_text.split('\n'):
        block = block.strip()
        if not block:
            continue
        if block.endswith(':') and block.startswith('/'):
            current_meta_path = block[:-1]
        elif block.startswith('{') and current_meta_path:
            try:
                meta = json.loads(block)
                meta_dir = os.path.dirname(current_meta_path).rstrip('/')
                meta_by_dir[meta_dir] = meta
            except (json.JSONDecodeError, ValueError):
                pass
            current_meta_path = None

    # Apply target_ns from meta to discovered sims
    for norm_dir, sim in discovered.items():
        for meta_dir, meta in meta_by_dir.items():
            if _normalize_sim_dir(norm_dir) == _normalize_sim_dir(meta_dir):
                if 'target_ns' in meta:
                    sim['target_ns'] = meta['target_ns']
                if 'launch_cmd' in meta:
                    sim['launch_cmd'] = meta['launch_cmd']
                if 'script' in meta and not sim.get('script'):
                    sim['script'] = meta['script']
                break

    # Update persistent cache with newly discovered sims
    for norm_dir, sim in discovered.items():
        _discovered_cache[_normalize_sim_dir(norm_dir)] = sim

    # Include cached sims that weren't found this time (process stopped, log aged out)
    for cached_key, cached_sim in _discovered_cache.items():
        norm_dir = cached_sim['directory'].rstrip('/')
        if norm_dir not in discovered:
            discovered[norm_dir] = cached_sim

    return list(discovered.values())


def _normalize_sim_dir(path):
    """Normalize a simulation directory path for comparison."""
    p = path.rstrip('/')
    # Strip ~/  or /home/<user>/ prefix to get a comparable suffix
    if p.startswith('~/'):
        return p[2:]
    # Match /home/<anything>/ prefix
    if p.startswith('/home/'):
        parts = p.split('/', 3)
        if len(parts) >= 4:
            return parts[3]
    # Match /root/ prefix
    if p.startswith('/root/'):
        return p[6:]
    return p


def merge_simulations(manual, discovered):
    """Merge manual config with auto-detected simulations. Manual takes precedence."""
    manual_suffixes = set()
    for s in manual:
        manual_suffixes.add(_normalize_sim_dir(s['directory']))

    merged = list(manual)
    for d in discovered:
        if _normalize_sim_dir(d['directory']) not in manual_suffixes:
            merged.append(d)
    return merged


def poll_gpu(host):
    """Poll GPU stats from the remote host."""
    command = "/usr/lib/wsl/lib/nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,name --format=csv,noheader,nounits 2>/dev/null || nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,name --format=csv,noheader,nounits"
    output = ssh_run(host, command)

    if output is None:
        return {'error': 'SSH connection failed'}

    try:
        parts = [p.strip() for p in output.split(',')]
        return {
            'gpu_util': int(parts[0]),
            'mem_util': int(parts[1]),
            'mem_used_mb': int(parts[2]),
            'mem_total_mb': int(parts[3]),
            'temperature': int(parts[4]),
            'name': parts[5] if len(parts) > 5 else 'Unknown',
        }
    except (ValueError, IndexError):
        return {'error': f'Failed to parse: {output}'}


def poll_all(host, simulations):
    """Poll all simulations and GPU in sequence (one SSH connection each)."""
    try:
        gpu = poll_gpu(host)
    except Exception:
        gpu = {'error': 'GPU poll failed'}

    # Auto-discover simulations and merge with manual config
    try:
        discovered = discover_simulations(host)
    except Exception:
        discovered = []
    all_sims = merge_simulations(simulations, discovered)

    results = {
        'timestamp': datetime.now().isoformat(),
        'simulations': [],
        'gpu': gpu,
    }
    for sim in all_sims:
        try:
            sim_result = poll_simulation(host, sim)
            if sim.get('_auto_detected'):
                sim_result['_auto_detected'] = True
            results['simulations'].append(sim_result)
        except Exception as e:
            import traceback
            debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug.log')
            with open(debug_path, 'a') as f:
                f.write(f"\npoll_simulation EXCEPTION for {sim['name']}: {e}\n")
                traceback.print_exc(file=f)
            results['simulations'].append({'name': sim['name'], 'status': 'error', 'percent': 0})
    return results
