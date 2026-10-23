#!/usr/bin/env python3
"""Run and preserve controlled dataset-knob sweeps; render existing results without rerunning."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from design import HERE, ROOT, sweeps
from metrics import summarize
import dataset_presets
from experiment_cleanup import clean_experiment, mark_experiment

BIN = ROOT / 'ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized'


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(clean(value), indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = list((ROOT/'scratch/sat-bs-handset').rglob('*.cc'))
    paths += list((ROOT/'scratch/sat-bs-handset').rglob('*.h'))
    paths += list((ROOT/'scratch/sat-bs-handset/config').glob('*.json'))
    paths += list((ROOT/'patches').glob('*.patch'))
    paths += list(HERE.glob('*.py')) + list(HERE.glob('*.json'))
    paths += [ROOT/'scripts/dataset'/name for name in
              ('full_dataset.json', 'campaign_config.py', 'dataset_presets.py', 'run_dataset.py', 'experiment_cleanup.py')]
    paths += list((ROOT/'experiments/queue-calibration').glob('*.py'))
    paths += [BIN]
    paths += list((ROOT/'ns-3-dev/build/lib').glob('libns3*.so'))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def verify(out, manifest):
    if sources() != manifest['sourceHashes']:
        raise RuntimeError('Sources/binary changed. Use --clean to restart in this directory or choose a fresh --out-dir; '
                           '--analyze-only can still render the preserved run.')
    for rec in manifest['runs']:
        if digest(out/rec['config']) != rec['configSha256']:
            raise RuntimeError('Saved configuration changed: '+rec['config'])
    if digest(out/'calibration-reference.json') != manifest['calibrationSha256']:
        raise RuntimeError('Saved queue reference changed')


def reference(out, supplied, jobs):
    if supplied:
        path = supplied.resolve()
    else:
        calibration_dir = out/(out.name+'-calibration')
        path = calibration_dir/'reference.json'
        if not path.exists():
            command = [sys.executable, str(ROOT/'experiments/queue-calibration/calibrate.py'),
                       '--out-dir', str(calibration_dir), '--run', '--analyze',
                       '--reference', str(path), '--jobs', str(jobs)]
            if not (calibration_dir/'manifest.json').exists():
                command.append('--plan')
            subprocess.run(command, cwd=ROOT, check=True)
    ref = json.loads(path.read_text())
    if not ref['accepted']:
        raise ValueError('Queue calibration must be accepted')
    for name, checksum in ref['modelHashes'].items():
        if name.startswith(('scratch/', 'patches/')) and digest(ROOT/name) != checksum:
            raise ValueError('Queue reference is stale: '+name)
    return path, ref


def plan(out, settings, reference_path, calibration, only=None):
    groups = sweeps(settings, calibration)
    if only:
        unknown = set(only) - {g['key'] for g in groups}
        if unknown:
            raise ValueError('Unknown groups: '+', '.join(sorted(unknown)))
        groups = [g for g in groups if g['key'] in only]
    mark_experiment(out, 'knob-sweep')
    settings = dict(settings)
    policy = settings.get('tcpBufferPolicy', 'default')
    settings['tcpBufferPolicy'] = policy
    settings['tcpSocketBufferBytes'] = tcp_socket_buffer_bytes(groups, calibration) if policy == 'socket-enlargement' else None
    settings['tcpSocketBufferPolicy'] = (
        'common power-of-two buffer >= 2*(largest planned/calibrated queue + path BDP)'
        if policy == 'socket-enlargement' else 'configured defaults except explicit socket-enlargement levels')
    configs = {}
    run_rows = []
    for group in groups:
        for level in group['levels']:
            cfg = level.pop('config')
            signature = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
            if signature not in configs:
                config_id = f'c{len(configs):03d}'
                configs[signature] = config_id
                for seed in settings['rngRuns']:
                    prefix = f'{out.name}-{config_id}-s{seed}'
                    if (ROOT/'results'/prefix).exists():
                        raise ValueError('Output already exists: '+prefix)
                    saved = json.loads(json.dumps(cfg))
                    saved['output']['prefix'] = prefix
                    config_path = Path('configs')/(prefix+'.json')
                    write_json(out/config_path, saved)
                    run_rows.append(dict(id=prefix, configId=config_id, rngRun=seed,
                        config=str(config_path), configSha256=digest(out/config_path)))
            level['configId'] = configs[signature]
    shutil.copyfile(reference_path, out/'calibration-reference.json')
    hashes = sources()
    for name in hashes:
        if name.startswith('ns-3-dev/'):
            continue
        target = out/'source-snapshot'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, target)
    import matplotlib
    import numpy
    manifest = dict(schemaVersion=1, createdUtc=datetime.now(timezone.utc).isoformat(),
        settings=settings, groups=groups, runs=run_rows, sourceHashes=hashes,
        calibrationSha256=digest(out/'calibration-reference.json'),
        environment=dict(python=sys.version, numpy=numpy.__version__, matplotlib=matplotlib.__version__),
        measurement=dict(startS=20, endS=119, transportWindowMs=settings['windowMs'],
            displayBinS=settings['displayBinS'], statisticalUnit='one simulation per independent RNG seed'))
    write_json(out/'manifest.json', manifest)
    patch = subprocess.run(['git', 'diff'], cwd=ROOT, text=True, capture_output=True, check=True)
    (out/'workspace.patch').write_text(patch.stdout)
    print(f"Planned {len(groups)} sweeps, {len(configs)} unique configurations, {len(run_rows)} runs", flush=True)
    return manifest


def tcp_socket_buffer_bytes(groups, calibration):
    """Cover planned queues and the largest calibrated multiplier at every bandwidth."""
    largest_queue = max(level['config']['application']['queueSizeBytes']
                        for group in groups for level in group['levels'])
    return dataset_presets.enlarged_socket_bytes(calibration, largest_queue)


def simulation_command(out, rec, config, settings):
    command = [str(BIN), '--config='+str(out/rec['config']), '--RngRun='+str(rec['rngRun'])]
    command += dataset_presets.socket_buffer_arguments(config, settings.get('tcpSocketBufferBytes'))
    return command


def compress_raw(directory):
    for path in sorted(directory.glob('*.csv')):
        target = path.with_suffix('.csv.gz')
        temporary = target.with_suffix('.gz.tmp')
        with path.open('rb') as source, gzip.open(temporary, 'wb', compresslevel=1) as destination:
            shutil.copyfileobj(source, destination)
        temporary.replace(target)
        path.unlink()  # Exact original bytes are retained in the gzip file.


def reuse_completed(out, manifest, previous):
    """Reuse immutable observations only when the physical model, exact config and seed match."""
    previous = previous.resolve()
    if previous == out:
        raise ValueError('--reuse must name a different experiment')
    old_path = previous/'manifest.json'
    old = json.loads(old_path.read_text())
    old_buffers = old.get('settings', {}).get('tcpSocketBufferBytes') or 131072
    new_buffers = manifest.get('settings', {}).get('tcpSocketBufferBytes') or 131072
    if old_buffers != new_buffers:
        raise ValueError('Cannot reuse runs: TCP endpoint buffer policies differ')
    def physical(hashes):
        return {name: value for name, value in hashes.items()
                if name.startswith(('ns-3-dev/', 'patches/')) or
                (name.startswith('scratch/') and name.endswith(('.cc', '.h')))}
    if physical(old['sourceHashes']) != physical(manifest['sourceHashes']):
        raise ValueError('Cannot reuse runs: physical sources, patches, binary or shared libraries differ')
    if old['measurement'] != manifest['measurement']:
        raise ValueError('Cannot reuse runs: measurement definitions differ')
    old_metrics = old['sourceHashes']['experiments/knob-sweep/metrics.py']
    if old_metrics != manifest['sourceHashes']['experiments/knob-sweep/metrics.py']:
        raise ValueError('Cannot reuse runs: metric extraction definitions differ')
    def signature(cfg):
        cfg = json.loads(json.dumps(cfg))
        cfg['output']['prefix'] = ''
        return json.dumps(cfg, sort_keys=True)
    available = {}
    for rec in old['runs']:
        status = previous/'runs'/(rec['id']+'.json')
        if not status.exists():
            continue
        row = json.loads(status.read_text())
        if row['exit'] not in (0, 1):
            continue
        cfg_path = previous/rec['config']
        if digest(cfg_path) != rec['configSha256']:
            raise ValueError('Previous saved config changed: '+rec['id'])
        available[signature(json.loads(cfg_path.read_text())), rec['rngRun']] = (rec, row)
    def link_or_copy(source, target):
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        return target
    reused = 0
    for index, rec in enumerate(manifest['runs']):
        if (out/'runs'/(rec['id']+'.json')).exists():
            continue
        cfg = json.loads((out/rec['config']).read_text())
        candidate = available.get((signature(cfg), rec['rngRun']))
        if not candidate:
            continue
        original, row = candidate
        prefix = original['id']
        cache = previous/'series'/(prefix+'.npz')
        if digest(cache) != row['cacheSha256']:
            raise ValueError('Previous metric cache changed: '+prefix)
        raw = previous/'raw'/prefix
        for name, checksum in row['rawSha256'].items():
            if digest(raw/name) != checksum:
                raise ValueError('Previous raw trace changed: '+prefix+'/'+name)
        for name in ('runs', 'series', 'logs', 'raw'):
            (out/name).mkdir(exist_ok=True)
        shutil.copytree(raw, out/'raw'/prefix, copy_function=link_or_copy)
        shutil.copy2(cache, out/'series'/(prefix+'.npz'))
        shutil.copy2(previous/'logs'/(prefix+'.log'), out/'logs'/(prefix+'.log'))
        shutil.copy2(previous/original['config'], out/rec['config'])
        updated = dict(rec, id=prefix, configSha256=digest(out/rec['config']),
            reusedFrom=dict(experiment=str(previous), runId=prefix,
                manifestSha256=digest(old_path), originalConfigSha256=original['configSha256']))
        saved = dict(row, **updated)
        manifest['runs'][index] = updated
        write_json(out/'runs'/(prefix+'.json'), saved)
        reused += 1
    provenance = out/'reused-provenance'
    provenance.mkdir(exist_ok=True)
    shutil.copy2(old_path, provenance/(previous.name+'-manifest.json'))
    failures = []
    for path in (previous/'logs').glob('*-exit.json'):
        result = json.loads(path.read_text())
        if result['exit'] in (0, 1):
            continue
        prefix = path.name.removesuffix('-exit.json')
        failures.append(dict(id=prefix, **result, originalLog=str(previous/'logs'/(prefix+'.log'))))
        shutil.copy2(previous/'logs'/(prefix+'.log'), provenance/(prefix+'-failed.log'))
    write_json(provenance/(previous.name+'-failures.json'), failures)
    origins = manifest.setdefault('reusedProvenance', [])
    if str(previous) not in origins:
        origins.append(str(previous))
    write_json(out/'manifest.json', manifest)
    print(f'Reused {reused} matching completed runs; prior simulator crashes retained separately: {len(failures)}', flush=True)


def run(out, manifest, jobs):
    verify(out, manifest)
    settings = manifest['settings']
    def one(rec):
        prefix = rec['id']
        status = out/'runs'/(prefix+'.json')
        if status.exists():
            saved = json.loads(status.read_text())
            if not (out/'series'/(prefix+'.npz')).exists():
                raise RuntimeError(prefix+': result cache missing; use --analyze-only --reextract')
            return saved
        config = json.loads((out/rec['config']).read_text())
        raw = out/'raw'/prefix
        exit_path = out/'logs'/(prefix+'-exit.json')
        if not exit_path.exists():
            original = ROOT/'results'/prefix
            if original.exists():
                raise RuntimeError(prefix+': incomplete simulation output exists; preserve it and use a fresh experiment directory')
            out.joinpath('logs').mkdir(exist_ok=True)
            before = time.monotonic()
            command = simulation_command(out, rec, config, settings)
            with (out/'logs'/(prefix+'.log')).open('w') as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            write_json(exit_path, dict(exit=result.returncode, wallS=time.monotonic()-before,
                                      command=command))
        exit_info = json.loads(exit_path.read_text())
        if exit_info['exit'] not in (0, 1):
            raise RuntimeError(prefix+': simulation crashed; inspect log')
        if not raw.exists():
            raw.parent.mkdir(exist_ok=True)
            shutil.move(str(ROOT/'results'/prefix), str(raw))
        values = summarize(raw, prefix, config, out/'series'/(prefix+'.npz'),
                           settings['windowMs'], settings['displayBinS'])
        log = (out/'logs'/(prefix+'.log')).read_text()
        events = [float(t) for t in re.findall(r'handover complete timeS=([\d.]+)', log)]
        saved = dict(rec, **exit_info, metrics=values, switchTimesS=events,
                     applicationPassed=exit_info['exit'] == 0)
        compress_raw(raw)
        saved['cacheSha256'] = digest(out/'series'/(prefix+'.npz'))
        saved['rawSha256'] = {p.name: digest(p) for p in sorted(raw.iterdir()) if p.is_file()}
        write_json(status, saved)
        return saved
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, rec): rec for rec in manifest['runs']}
        errors = []
        for count, future in enumerate(as_completed(futures), 1):
            try:
                rec = future.result()
                print(f"[{count}/{len(futures)}] {rec['id']} exit={rec['exit']} wall={rec['wallS']:.1f}s", flush=True)
            except Exception as error:
                rec = futures[future]
                errors.append(dict(id=rec['id'], error=str(error)))
                print(f"[{count}/{len(futures)}] ERROR {rec['id']}: {error}", flush=True)
    if errors:
        write_json(out/'run-errors.json', errors)
        raise RuntimeError(f'{len(errors)} runs failed; see run-errors.json and preserved logs')
    verify(out, manifest)
    write_json(out/'run-outcomes.json', dict(runs=[json.loads((out/'runs'/(r['id']+'.json')).read_text()) for r in manifest['runs']]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=ROOT/'results/knob-sweep',
                        help='fixed experiment directory (default: results/knob-sweep); existing plans resume')
    parser.add_argument('--clean', action='store_true', help='delete this experiment and its recorded raw outputs before planning again; omit to resume')
    parser.add_argument('--reference', type=Path, help='accepted queue reference for the current model; otherwise calibrate automatically')
    parser.add_argument('--reuse', type=Path, nargs='+', help='reuse completed exact config/seed matches with an identical physical model and metric extractor')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--seeds', type=int, nargs='+', help='override independent RNG runs for a new plan')
    parser.add_argument('--only', nargs='+', help='new plan: select sweep keys (default: all configured sweeps)')
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--tcp-buffer-policy', choices=['default', 'socket-enlargement'],
                        help='new plan: configured TCP limits except the socket knob (default), or enlarge sockets across other sweeps')
    parser.add_argument('--analyze-only', action='store_true')
    parser.add_argument('--reextract', action='store_true', help='analysis: recompute metrics from preserved compressed raw traces')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.out_dir.resolve()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', out.name):
        parser.error('output directory name must contain only letters, digits, dots, underscores or hyphens')
    if args.clean:
        if args.analyze_only or args.reextract:
            parser.error('--clean cannot be combined with analysis-only options')
        for protected in ([args.reference] if args.reference else []) + (args.reuse or []):
            if protected.resolve().is_relative_to(out):
                parser.error('--reference/--reuse must be outside the directory being cleaned')
        try:
            clean_experiment(out, ROOT/'results', 'knob-sweep')
        except ValueError as error:
            parser.error(str(error))
    if args.analyze_only:
        from plot import analyze
        analyze(out, reextract=args.reextract)
        return
    subprocess.run(['./ns3', 'build', 'sat-bs-handset', 'queue-calibration', '-j', str(min(args.jobs, 4))], cwd=ROOT/'ns-3-dev', check=True)
    manifest_path = out/'manifest.json'
    if manifest_path.exists():
        if args.seeds or args.only or args.reference or args.tcp_buffer_policy:
            parser.error('existing plan: omit --seeds/--only/--reference/--tcp-buffer-policy to resume; add --clean to restart')
        manifest = json.loads(manifest_path.read_text())
        verify(out, manifest)
    else:
        out.mkdir(parents=True, exist_ok=True)
        settings = json.loads((HERE/'settings.json').read_text())
        settings['tcpBufferPolicy'] = args.tcp_buffer_policy or 'default'
        if args.seeds:
            if len(set(args.seeds)) != len(args.seeds) or min(args.seeds) < 1:
                parser.error('--seeds must be unique positive integers')
            settings['rngRuns'] = args.seeds
        if settings['windowMs'] != 50 or settings['displayBinS'] != 1:
            parser.error('this experiment defines 50 ms transport windows and 1 s display/KPI bins')
        path, calibration = reference(out, args.reference, args.jobs)
        manifest = plan(out, settings, path, calibration, args.only)
    if args.reuse:
        for previous in args.reuse:
            reuse_completed(out, manifest, previous)
    if not args.plan_only:
        run(out, manifest, args.jobs)
        from plot import analyze
        analyze(out)


if __name__ == '__main__':
    main()
