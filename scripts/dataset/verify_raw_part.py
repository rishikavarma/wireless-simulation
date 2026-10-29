#!/usr/bin/env python3
"""Verify a transferred raw archive against its worker completion marker."""
import argparse
import hashlib
import re
from pathlib import Path


def verify(archive: Path, marker: Path):
    match = re.fullmatch(r'([0-9a-f]{64}) ([0-9]+) raw runs\n', marker.read_text())
    if not match or not archive.is_file() or archive.stat().st_size == 0:
        raise ValueError('raw part or completion marker is incomplete')
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != match.group(1):
        raise ValueError('raw part checksum differs from worker marker')
    return int(match.group(2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('marker', type=Path)
    args = parser.parse_args()
    print(f'verified {verify(args.archive, args.marker)} raw runs')


if __name__ == '__main__':
    main()
