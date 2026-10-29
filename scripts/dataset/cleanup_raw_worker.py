#!/usr/bin/env python3
"""Remove raw VM directories only after their compressed part is complete."""
import argparse
import json
import shutil
from pathlib import Path

from stream_parts import planned_parts

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--archive-dir', type=Path, required=True)
    parser.add_argument('--part', type=int, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.campaign/'manifest.json').read_text())
    parts, _ = planned_parts(manifest, 64, 64)
    if not 0 <= args.part < len(parts):
        parser.error('invalid part')
    archive = args.archive_dir/f'part-{args.part:05d}.tar.gz'
    marker = args.archive_dir/f'part-{args.part:05d}.done'
    if not archive.is_file() or archive.stat().st_size == 0 or not marker.is_file():
        raise ValueError('compressed part is incomplete; raw directories retained')
    for record in parts[args.part]:
        prefix = record['prefix']
        if not prefix or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.'
                             for c in prefix):
            raise ValueError('unsafe run prefix')
        raw = ROOT/'results'/prefix
        if raw.is_symlink():
            raise ValueError('refusing to remove a symlink')
        if raw.is_dir():
            shutil.rmtree(raw)
    print(f'cleaned raw VM directories for part {args.part}')


if __name__ == '__main__':
    main()
