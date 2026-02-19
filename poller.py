"""
SSH poller for remote simulation monitoring.
Connects to the remote host via subprocess ssh, collects simulation
status, log data, process info, and GPU stats in a single SSH call.
"""

import csv
import io
import os
import subprocess
import time
from datetime import datetime, timedelta


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
    commands = [
        f"echo '===TAIL==='",
        f"tail -1 {log_path} 2>/dev/null || echo 'NO_LOG'",
        f"echo '===HISTORY==='",
        f"tail -500 {log_path} 2>/dev/null || echo 'NO_LOG'",
        f"echo '===PROCESS==='",
        f"pgrep -af '{script_name}' 2>/dev/null || echo 'NOT_RUNNING'",
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

    # Check process
    process_text = sections.get('PROCESS', '')
    process_running = process_text.strip() != 'NOT_RUNNING' and process_text.strip() != ''

    # Log tail for display
    log_tail_text = sections.get('LOGTAIL', '')
    log_tail = []
    if log_tail_text and log_tail_text != 'NO_LOG':
        log_tail = [l for l in log_tail_text.split('\n') if l.strip()]

    # Calculate progress and ETA
    current_ns = last_entry['time_ns'] if last_entry else 0
    percent = min(100.0, (current_ns / target_ns) * 100) if target_ns > 0 else 0
    speed = last_entry['speed_ns_day'] if last_entry else None

    eta = None
    if speed and speed > 0 and current_ns < target_ns:
        remaining_ns = target_ns - current_ns
        remaining_days = remaining_ns / speed
        eta = datetime.now() + timedelta(days=remaining_days)

    # Determine status — use speed > 0 as a fallback indicator that the sim is running
    if percent >= 100:
        status = 'completed'
    elif process_running or (speed and speed > 0):
        status = 'running'
    else:
        status = 'stopped'

    return {
        'name': name,
        'status': status,
        'current_ns': round(current_ns, 1),
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
    }


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
    results = {
        'timestamp': datetime.now().isoformat(),
        'simulations': [],
        'gpu': gpu,
    }
    for sim in simulations:
        try:
            results['simulations'].append(poll_simulation(host, sim))
        except Exception as e:
            import traceback
            debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug.log')
            with open(debug_path, 'a') as f:
                f.write(f"\npoll_simulation EXCEPTION for {sim['name']}: {e}\n")
                traceback.print_exc(file=f)
            results['simulations'].append({'name': sim['name'], 'status': 'error', 'percent': 0})
    return results
