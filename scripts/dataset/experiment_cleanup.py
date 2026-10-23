"""Explicit cleanup of an experiment and the raw outputs it owns."""
import json
from pathlib import Path
import re
import shutil

MARKER = '.experiment-owner.json'


def mark_experiment(out, kind):
    out.mkdir(parents=True, exist_ok=True)
    (out/MARKER).write_text(json.dumps({'kind': kind})+'\n')


def clean_experiment(out, results, kind):
    """Remove a selected experiment, never a shared results/repository directory.

    Raw output names come from saved configs/manifests, not prefix globs. Reused
    observations live inside the selected experiment; their original runs stay.
    """
    out, results = Path(out).resolve(), Path(results).resolve()
    if out == results or results.is_relative_to(out) or out.parent == Path('/'):
        raise ValueError('refusing to clean a shared results or parent directory')
    if not out.exists():
        return
    if not out.is_dir():
        raise ValueError('experiment output must be a directory')
    marker, manifest = out/MARKER, out/'manifest.json'
    try:
        metadata = json.loads(manifest.read_text()) if manifest.exists() else {}
    except json.JSONDecodeError:
        if not marker.exists():
            raise ValueError('cannot identify an experiment with an incomplete manifest')
        metadata = {}
    detected = (json.loads(marker.read_text()).get('kind') if marker.exists() else
                'knob-sweep' if 'groups' in metadata and 'runs' in metadata else
                'dataset' if 'preset' in metadata and 'runs' in metadata else None)
    if detected != kind and any(out.iterdir()):
        raise ValueError('refusing to clean an unrecognized or different experiment directory')
    reused = {r['id'] for r in metadata.get('runs', []) if 'reusedFrom' in r}
    prefixes = set()
    for rec in metadata.get('runs', []):
        if 'reusedFrom' not in rec:
            prefixes.add(rec.get('prefix', rec.get('id', '')))
    # Also cover an interrupted plan and nested automatic queue calibration.
    for config in out.rglob('configs/*.json'):
        try:
            prefix = json.loads(config.read_text()).get('output', {}).get('prefix', '')
        except json.JSONDecodeError:
            prefix = config.stem  # The plan may have been interrupted mid-write.
        prefixes.add(prefix)
    targets = []
    for prefix in prefixes - reused - {''}:
        if prefix in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', prefix):
            raise ValueError('unsafe raw output prefix: '+repr(prefix))
        if kind == 'knob-sweep' and not prefix.startswith(out.name+'-'):
            continue  # Never remove an original observation reused from another sweep.
        target = results/prefix
        if target == out or out.is_relative_to(target):
            raise ValueError('raw output overlaps experiment directory')
        targets.append(target)
    removed = 0
    for target in targets:
        if target.is_symlink() or target.is_file():
            target.unlink()
            removed += 1
        elif target.exists():
            shutil.rmtree(target)
            removed += 1
    shutil.rmtree(out)
    print(f'Cleaned {out} and {removed} recorded raw output entries', flush=True)
