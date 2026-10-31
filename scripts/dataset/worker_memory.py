"""Leave headroom for the worker, OS and packing when limiting simulations."""
from pathlib import Path


def memory_budget():
    fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    total = min(int(fields['MemTotal'].split()[0]),
                int(fields.get('MemAvailable', fields['MemTotal']).split()[0])) * 1024
    # Respect cgroup limits as well as physical RAM.
    for name in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            limit = int(Path(name).read_text())
            usage_name = name.replace('memory.max', 'memory.current').replace('memory.limit_in_bytes', 'memory.usage_in_bytes')
            try:
                limit -= int(Path(usage_name).read_text())
            except OSError:
                pass
            total = min(total, max(0, limit))
        except (OSError, ValueError):
            pass
    return total


def simulation_limit(jobs):
    if jobs < 1:
        raise ValueError('jobs must be positive')
    return int(memory_budget() * 0.70) // jobs
