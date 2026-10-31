#!/usr/bin/env python3
"""Verify and extract a worker's raw metric archive into the local campaign."""
import argparse
import hashlib
import json
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from stream_parts import planned_parts
from run_completion import validate_membership

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_sources(campaign, hashes):
    manifest = json.loads((campaign/'manifest.json').read_text())
    configs = {rec['prefix']: sha256(campaign/'configs'/(rec['prefix']+'.json'))
               for rec in manifest['runs']}
    if hashes.get('configs') != configs:
        raise ValueError('worker configurations differ from the local plan')
    special = {'binary': ROOT/'ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized',
               'nrLibrary': ROOT/'ns-3-dev/build/lib/libns3.48-nr-optimized.so',
               'ns3Patch': ROOT/'patches/ns3-v3.48-local-ntn.patch',
               'nrPatch': ROOT/'patches/nr-v5.1-local-ntn.patch'}
    for name, expected in hashes.items():
        if name == 'configs':
            continue
        path = special.get(name, ROOT/name)
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'worker source differs from local source: {name}')


def unpack(campaign, archive, source_hashes_path):
    campaign = campaign.resolve()
    archive = archive.resolve()
    manifest = json.loads((campaign/'manifest.json').read_text())
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        assignments = [m for m in members if PurePosixPath(m.name).name.startswith(
            'raw-assignment-') and m.name.endswith('.json')]
        if len(assignments) != 1:
            raise ValueError('expected exactly one raw assignment')
        assignment = json.load(bundle.extractfile(assignments[0]))
        if assignment['format'] != 'raw-worker-v1' or assignment['campaign'] != campaign.name:
            raise ValueError('archive belongs to another campaign')
        parts, _ = planned_parts(manifest, assignment['batchRuns'], assignment['calibrationRuns'])
        start, stop = assignment['partStart'], assignment['partStop']
        if not 0 <= start < stop <= len(parts):
            raise ValueError('invalid assigned part range')
        group = [rec for group in parts[start:stop] for rec in group]
        validate_membership(group, assignment)
        if stop != start+1:raise ValueError('expected a single sealed part')
        run_ids = assignment['runs']
        hashes = json.loads(source_hashes_path.read_text())
        source_target = campaign/'source-hashes.json'
        if source_target.exists():
            if sha256(source_target) != sha256(source_hashes_path):
                raise ValueError('worker source hashes disagree with previous workers')
        else:
            verify_sources(campaign, hashes)
            shutil.copy2(source_hashes_path, source_target)
        allowed_files = {assignments[0].name}
        allowed_files.update(f'results/{campaign.name}/logs/{run_id}{suffix}'
                             for run_id in run_ids for suffix in ('.json', '.log'))
        allowed_dirs = tuple(f'results/{run_id}/' for run_id in run_ids)
        seen = set()
        for member in members:
            name = member.name
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or not member.isfile() and
                    not member.isdir()):
                raise ValueError('unsafe archive entry: '+name)
            if member.isdir():
                continue
            if name in seen or not (name in allowed_files or
                                    any(name.startswith(directory) for directory in allowed_dirs)):
                raise ValueError('unexpected or duplicate archive entry: '+name)
            seen.add(name)
        if not allowed_files <= seen:
            raise ValueError('worker archive is missing metadata or logs')
        for run_id in run_ids:
            for suffix in ('tcp.csv', 'summary.txt', 'app.txt'):
                name = f'results/{run_id}/{run_id}-{suffix}'
                if name not in seen:
                    raise ValueError('worker archive is missing a raw metric file: '+name)
        for member in members:
            if not member.isfile():
                continue
            rel = PurePosixPath(member.name)
            target = (campaign.joinpath(*rel.parts[2:])
                      if rel.parts[:2] == ('results', campaign.name)
                      else ROOT/member.name)
            if any(parent.is_symlink() for parent in target.parents if parent != ROOT.parent):
                raise ValueError('symlink in extraction destination: '+str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            incoming = target.with_name(target.name+'.incoming')
            with bundle.extractfile(member) as source, incoming.open('wb') as output:
                shutil.copyfileobj(source, output)
            if target.exists():
                if sha256(incoming) != sha256(target):
                    incoming.unlink()
                    raise ValueError('conflicting existing file: '+str(target))
                incoming.unlink()
            else:
                incoming.replace(target)
    sealed = campaign/'sealed-parts'/f'{start:05d}.json'
    sealed.parent.mkdir(exist_ok=True)
    if sealed.exists() and json.loads(sealed.read_text()) != assignment:
        raise ValueError('sealed part membership changed')
    temporary = sealed.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(assignment, indent=2)+'\n')
    temporary.replace(sealed)
    print(f'unpacked {len(run_ids)} raw runs from parts [{start}, {stop})')
    return assignment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--source-hashes', type=Path, required=True)
    args = parser.parse_args()
    unpack(args.campaign, args.archive, args.source_hashes)


if __name__ == '__main__':
    main()
