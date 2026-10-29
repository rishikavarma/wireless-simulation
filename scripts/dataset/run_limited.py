#!/usr/bin/env python3
"""Run a coordinator command with a deadline on macOS and Linux."""
import argparse
import os
import signal
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('seconds', type=int)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.seconds < 1 or not args.command:
        parser.error('provide a positive timeout and a command')
    process = subprocess.Popen(args.command, start_new_session=True)
    try:
        return process.wait(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
