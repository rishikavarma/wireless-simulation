#!/usr/bin/env python3
"""Use the collector's pinned SSH config for private worker transfers."""
import os
import argparse
import subprocess
import sys


def command(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('ssh', 'scp'))
    parser.add_argument('--zone', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--command')
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args(argv)
    common = ['-F', os.path.expanduser('~/.ssh/dataset_config'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
              '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30',
              '-o', 'ServerAliveCountMax=3']
    if args.operation == 'ssh':
        if len(args.paths) != 1 or args.command is None:
            parser.error('SSH requires one worker and a remote command')
        return ['ssh', *common, args.paths[0], args.command]
    if args.command is not None or len(args.paths) != 2:
        parser.error('SCP requires one source and one destination')
    # Existing remote paths use ~; legacy SCP expands them consistently.
    return ['scp', '-O', *common, *args.paths]


if __name__ == '__main__':
    raise SystemExit(subprocess.run(command(sys.argv[1:]), stdin=subprocess.DEVNULL).returncode)
