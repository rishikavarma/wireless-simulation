#!/usr/bin/env python3
"""Apply a per-simulation address-space ceiling without preexec in a thread."""
import os
import resource
import sys

if sys.platform == 'linux':
    import ctypes
    import signal
    parent = os.getppid()
    # Terminate a simulation if its Python coordinator is OOM-killed.
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise RuntimeError('could not set parent-death signal')
    if parent == 1 or os.getppid() != parent:
        raise SystemExit('simulation coordinator exited')
limit = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
# Avoid core dumps filling the worker disk after allocation failure.
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.execv(sys.argv[2], sys.argv[2:])
