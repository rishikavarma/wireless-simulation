"""Physical units and 50 ms transport windows; primary-UE event-weighted PHY metrics."""
import csv
import gzip
import math
from pathlib import Path

import numpy as np

METRICS = [
    ('srtt_ms', '50 ms mean SRTT state (may be stale)', 'ms'),
    ('rttvar_ms', '50 ms rolling mean RTTVAR', 'ms'),
    ('loss50', 'TCP loss detected in trailing 50 ms', 'MSS equivalents'),
    ('cumulative_loss', 'TCP loss since measurement start', 'MSS equivalents'),
    ('sinr_ul_db', 'Received UL SINR', 'dB'),
    ('sinr_dl_db', 'Received DL SINR', 'dB'),
    ('mcs_data', 'Scheduled MCS, data direction', 'index'),
    ('tb_fail_data_pct', 'TB decode failures, data direction', '%'),
]


def records(path):
    path = Path(path)
    if not path.exists():
        path = path.with_suffix(path.suffix + '.gz')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', newline='') as handle:
        yield from csv.DictReader(line for line in handle if line.strip() and not line.startswith('#'))


def mean(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(values.mean()) if len(values) else math.nan


def rolling_mean(times, values, seconds):
    """Sample mean over (t - seconds, t], retaining unavailable values as NaN."""
    left = np.searchsorted(times, times-seconds+1e-9, side='right')
    valid = np.isfinite(values)
    sums = np.r_[0., np.cumsum(np.where(valid, values, 0.))]
    counts = np.r_[0, np.cumsum(valid)]
    denominator = counts[1:] - counts[left]
    return np.divide(sums[1:]-sums[left], denominator,
                     out=np.full(len(times), np.nan), where=denominator > 0)


def trailing_loss(times, cumulative, seconds):
    """Difference in the cumulative TCP detection counter, never an average of it."""
    indices = np.searchsorted(times, times-seconds+1e-9, side='right')-1
    result = np.full(len(times), np.nan)
    valid = indices >= 0
    result[valid] = cumulative[valid] - cumulative[indices[valid]]
    if np.any(result[np.isfinite(result)] < -1e-6):
        raise ValueError('cumulative TCP loss counter went backwards')
    return result


def summarize(directory, prefix, cfg, cache_path, window_ms=50, display_bin_s=1):
    start = cfg['time']['warmUp'] + cfg['time']['measureStart']
    end = cfg['time']['simTime']-cfg['time']['appDrain']-cfg['time']['measureEnd']
    bins = int(round((end-start)/display_bin_s))
    centers = start + (np.arange(bins)+.5)*display_bin_s
    tcp = list(records(directory/f'{prefix}-tcp.csv'))
    times = np.array([float(r['time_s']) for r in tcp])
    # Stop() can commit at a timestamp already sampled by the periodic poll.
    order = np.argsort(times, kind='stable')
    times = times[order]
    keep = np.r_[times[1:] != times[:-1], True] if len(times) else np.array([], dtype=bool)
    order = order[keep]
    times = times[keep]
    srtt = np.array([float(tcp[i]['srtt']) * 100 for i in order])
    variance = np.array([float(tcp[i]['rttvar']) for i in order])
    cumulative = np.array([float(tcp[i]['lost']) * 100 for i in order])
    if cfg['application'].get('transport', 'tcp') == 'udp':
        if np.any(np.isfinite(srtt)) or np.any(np.isfinite(cumulative)):
            raise ValueError(f'{prefix}: fabricated TCP fields for UDP')
    variance[srtt <= 0] = np.nan
    srtt[srtt <= 0] = np.nan
    window = window_ms/1000
    signals = dict(srtt_ms=rolling_mean(times, srtt, window),
                   rttvar_ms=rolling_mean(times, variance, window),
                   loss50=trailing_loss(times, cumulative, window),
                   cumulative_loss=cumulative-cumulative[0] if len(cumulative) else cumulative)
    selected = (times >= start) & (times < end)
    indices = np.floor((times-start)/display_bin_s+1e-8).astype(int)
    series = {'time_s': centers}
    summary = {}
    for key, values in signals.items():
        series[key] = np.array([mean(values[selected & (indices == i)]) for i in range(bins)])
        valid = values[selected & np.isfinite(values)]
        summary[key] = float(valid[-1]) if key == 'cumulative_loss' and len(valid) else mean(valid)
        summary[key+'_p90'] = float(np.percentile(valid, 90)) if len(valid) else math.nan
        summary[key+'_observed'] = len(valid)
    # Explicit transport progress complements held RTT state. Older collections
    # lack these fields; keep them unavailable rather than inventing activity.
    for key in ('tcp_pending_bytes', 'tcp_ack_age_s', 'tcp_no_progress_s', 'tcp_rtt_update_age_s'):
        values = np.array([float(tcp[i].get(key, 'nan')) for i in order])
        series[key] = np.array([mean(values[selected & (indices == i)]) for i in range(bins)])
        valid = values[selected & np.isfinite(values)]
        summary[key] = mean(valid)
        summary[key+'_max'] = float(valid.max()) if len(valid) else math.nan
    kpis = [r for r in records(directory/f'{prefix}-kpis.csv')
            if r['ue_index'] == '0' and start < float(r['time_s']) <= end]
    expected = start + np.arange(1, bins+1)*display_bin_s
    if len(kpis) != bins or any(abs(float(r['interval_s'])-display_bin_s) > 1e-6 for r in kpis) or not np.allclose([float(r['time_s']) for r in kpis], expected):
        raise ValueError(f'{prefix}: incomplete primary KPI intervals')
    identities = {identity for r in kpis for identity in r.get('rnti_history',r['rnti']).split('|')
                  if identity and int(identity)>0}
    summary['primary_rnti_count'] = len(identities)
    # Store numerator/denominator independently so idle bins never become zero
    # SINR/MCS/failure, and failure fractions are weighted by received attempts.
    sums = {(kind, direction): np.zeros(bins) for kind in ('sinr', 'mcs', 'fail') for direction in ('ul', 'dl')}
    counts = {(kind, direction): np.zeros(bins, dtype=int) for kind in ('sinr', 'mcs', 'fail') for direction in ('ul', 'dl')}
    for row in records(directory/f'{prefix}-transport-blocks.csv'):
        t = float(row['time_s'])
        if row['rnti'] not in identities or not start <= t < end:
            continue
        i = min(bins-1, int((t-start)/display_bin_s))
        direction = row['direction'].lower()
        fields = [('mcs', float(row['mcs']))] if row['event'] == 'TX' else [
            ('sinr', float(row['sinr_db'])), ('fail', 1-float(row['crc_pass_model']))]
        for kind, value in fields:
            if math.isfinite(value):
                sums[kind, direction][i] += value
                counts[kind, direction][i] += 1
    for direction in ('ul', 'dl'):
        for kind, suffix, scale in [('sinr', '_db', 1), ('mcs', '', 1), ('fail', '_pct', 100)]:
            key = ('tb_fail_' if kind == 'fail' else kind+'_') + direction + suffix
            numerator, denominator = sums[kind, direction], counts[kind, direction]
            series[key] = np.divide(numerator*scale, denominator,
                out=np.full(bins, np.nan), where=denominator > 0)
            summary[key] = float(numerator.sum()*scale/denominator.sum()) if denominator.sum() else math.nan
            summary[key+'_observed'] = int(denominator.sum())
            series[key+'_count'] = denominator
        field = 'DRB.UEThp'+('Ul' if direction == 'ul' else 'Dl')+'_Mbps'
        series['pdcp_'+direction+'_kbps'] = np.array([float(r[field])*1000 for r in kpis])
        summary['pdcp_'+direction+'_kbps'] = mean(series['pdcp_'+direction+'_kbps'])
        suffix = direction
        for unit in ('pdus', 'bytes'):
            field = 'rlc_tx_drop_'+suffix+'_'+unit
            # Missing columns in historical runs are unavailable, not zero.
            series[field] = np.array([float(r.get(field, math.nan)) for r in kpis])
            summary[field] = float(series[field].sum())
    direction = cfg['application'].get('direction', 'ul')
    for alias, key in [('mcs_data', 'mcs_'+direction), ('tb_fail_data_pct', 'tb_fail_'+direction+'_pct')]:
        series[alias], summary[alias] = series[key], summary[key]
    paths = [r for r in records(directory/'ue-paths.csv') if r['ue_index'] == '0' and start <= float(r['time_s']) < end]
    summary['path_visible_fraction'] = mean([float(r['visible']) for r in paths])
    summary['min_service_elevation_deg'] = min((float(r['service_elevation_deg']) for r in paths), default=math.nan)
    summary['transport_sample_count'] = int(selected.sum())
    summary['transport_observed_fraction'] = float(np.isfinite(srtt[selected]).mean()) if selected.any() else 0.0
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the 5 ms-sampled rolling curves as well as readable 1 s display bins.
    np.savez_compressed(cache_path, **series, raw_time_s=times[selected],
                        **{'raw_'+key: values[selected] for key, values in signals.items()})
    return summary
