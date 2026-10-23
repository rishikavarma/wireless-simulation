#!/usr/bin/env python3
"""Check connected-mode access recovery using the nine original sweep cases."""
import argparse
import concurrent.futures
import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/dataset'))
import dataset_presets
from run_dataset import BIN, profiles


def records(path):
    if not path.exists():
        path = path.with_suffix(path.suffix+'.gz')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as handle:
        return list(csv.DictReader(line for line in handle if not line.startswith('#')))


def cases():
    base = copy.deepcopy(next(cfg for _, _, cfg in profiles() if cfg['application']['workload']=='cbr'))
    base['application'].update(dataRate='32kbps', queueSizeBdp=2, queueSizeBytes=179476)
    base['handover']['enabled'] = True
    base['background'] = dict(count=0, relay=0, dataRate='32kbps', distanceM=500)
    result = []
    for count in (0, 1, 3, 8):
        for seed in (30001, 30002, 30003):
            cfg = copy.deepcopy(base)
            cfg['background']['count'] = count
            result.append((f'switch-{count}ue-s{seed}', seed, cfg, []))
    for count in (1, 3, 8):
        cfg = copy.deepcopy(base)
        cfg['background']['count'] = count
        cfg['handover']['enabled'] = False
        result.append((f'fixed-{count}ue', 30001, cfg, []))
    for loss in (6, 9):
        for seed in (30001, 30002, 30003):
            cfg = copy.deepcopy(base)
            cfg['radio']['serviceExcessLossDb'] = loss
            result.append((f'loss-{loss}db-s{seed}', seed, cfg, []))
    cfg = copy.deepcopy(base)
    cfg['background']['count'] = 1
    result.append(('forced-finite-sr', 30001, cfg, ['--ns3::NrUeMac::SrTransMax=1']))
    for workload, transport, bandwidth in [('cbr', 'udp', 10), ('iperf', 'tcp', 10), ('cbr', 'tcp', 20)]:
        cfg = copy.deepcopy(base)
        cfg['background']['count'] = 3
        cfg['application'].update(workload=workload, transport=transport)
        hz = bandwidth*1e6
        cfg['radio'].update(bandwidthHz=hz, dlBandwidthHz=hz, ulBandwidthHz=hz)
        if workload == 'iperf':
            cfg['application']['tcpSocketBufferBytes'] = 8388608
        result.append((f'control-{workload}-{transport}-{bandwidth}mhz', 30001, cfg, []))
    return result


def audit(out, name, cfg, exit_code):
    prefix = cfg['output']['prefix']
    directory = ROOT/'results'/prefix
    if exit_code not in (0, 1):
        raise AssertionError(f'{name}: simulator crash {exit_code}')
    app = records(directory/'primary-application.csv')
    kpis = [r for r in records(directory/(prefix+'-kpis.csv')) if r['ue_index']=='0']
    final = app[-1]
    before = next(r for r in app if float(r['time_s'])==73)
    after = next(r for r in app if float(r['time_s'])==119)
    post_bytes = int(after['rx_bytes'])-int(before['rx_bytes'])
    identities = {r['rnti'] for r in kpis if 20<=float(r['time_s'])<=119 and int(r['rnti'])>0}
    tcp = records(directory/(prefix+'-tcp.csv'))
    missing = lambda x: x in ('nan', '')
    if cfg['application'].get('transport', 'tcp')=='udp':
        assert all(missing(r['tcp_ack_age_s']) and missing(r['tcp_no_progress_s']) for r in tcp)
    else:
        assert all(float(r['tcp_pending_bytes'])>=0 for r in tcp)
        assert all(float(r['tcp_no_progress_s'])>=0 for r in tcp)
        assert all(float(r['tcp_no_progress_s'])==0 for r in tcp if float(r['tcp_pending_bytes'])==0)
    # Weak-link cases are observations, not required delivery successes.
    if cfg['radio']['serviceExcessLossDb'] <= 6:
        assert post_bytes>0, (name, 'no post-switch delivery')
        assert len(identities)==1, (name, 'established C-RNTI replaced', identities)
        if cfg['application']['workload']=='cbr' and cfg['application'].get('transport','tcp')=='tcp':
            assert int(final['rx_bytes'])/int(final['tx_bytes'])>.99, (name, final)
    events = records(directory/(prefix+'-radio-events.csv'))
    report = (directory/(prefix+'-app.txt')).read_text()
    assert 'application workload='+cfg['application']['workload'] in report
    assert 'accepted_bytes>0 and delivered_bytes>0' in report
    assert 'shortfall_includes_backlog' in report
    return dict(case=name, exit=exit_code, acceptedBytes=int(final['tx_bytes']),
        deliveredBytes=int(final['rx_bytes']), post73DeliveredBytes=post_bytes,
        establishedIdentities=sorted(identities),
        msg3=sum(r['event']=='CRNTI_MSG3' for r in events),
        decodedRecovery=sum(r['event']=='CRNTI_RECOVERY' for r in events),
        maxNoProgressS=max((float(r['tcp_no_progress_s']) for r in tcp
                            if not missing(r['tcp_no_progress_s'])), default=None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    selected = [c for c in cases() if not args.only or c[0] in args.only]
    if not selected or args.jobs<1:
        parser.error('select known cases and positive jobs')
    for sub in ('configs', 'logs', 'cases'):
        (out/sub).mkdir(parents=True, exist_ok=True)
    paths = [BIN, ROOT/'ns-3-dev/build/lib/libns3.48-nr-optimized.so',
             *list((ROOT/'scratch/sat-bs-handset').rglob('*.cc')),
             *list((ROOT/'scratch/sat-bs-handset').rglob('*.h'))]
    provenance = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    stamp = out/'source-hashes.json'
    if stamp.exists() and json.loads(stamp.read_text())!=provenance:
        raise RuntimeError('regression sources changed; choose fresh output')
    stamp.write_text(json.dumps(provenance, indent=2)+'\n')
    def one(case):
        name, seed, cfg, options = case
        status = out/'cases'/(name+'.json')
        if status.exists():
            return json.loads(status.read_text())
        cfg['output']['prefix'] = out.name+'-'+name
        config = out/'configs'/(name+'.json')
        config.write_text(json.dumps(cfg,indent=2)+'\n')
        command = [str(BIN),'--config='+str(config),'--RngRun='+str(seed),
                   *dataset_presets.socket_buffer_arguments(cfg),*options]
        start = time.monotonic()
        with (out/'logs'/(name+'.log')).open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        row = audit(out,name,cfg,completed.returncode)
        row.update(command=command,wallS=time.monotonic()-start)
        status.write_text(json.dumps(row,indent=2)+'\n')
        print(f"PASS {name}: post73={row['post73DeliveredBytes']} bytes, Msg3={row['msg3']}, decoded={row['decodedRecovery']}",flush=True)
        # Keep exact raw bytes with substantially less storage.
        for path in (ROOT/'results'/cfg['output']['prefix']).glob('*.csv'):
            with path.open('rb') as src, gzip.open(path.with_suffix('.csv.gz'),'wb',compresslevel=1) as dst:
                import shutil
                shutil.copyfileobj(src,dst)
            path.unlink()
        return row
    rows, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(one,c):c[0] for c in selected}
        for future in concurrent.futures.as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as error:
                errors.append(dict(case=futures[future],error=str(error)))
                print('FAIL',futures[future],str(error),flush=True)
    (out/'verification.json').write_text(json.dumps(dict(passed=len(rows),failed=len(errors),runs=rows,errors=errors),indent=2)+'\n')
    if errors:
        raise SystemExit(1)


if __name__=='__main__':
    main()
