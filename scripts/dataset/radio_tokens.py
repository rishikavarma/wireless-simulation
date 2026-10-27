"""Join ns-3 radio telemetry to the primary UE's minRTT token intervals.

Event and transport-block values summarize observations in [start, end). KPI
values are the latest *completed* one-second interval at token end. Missing
observations remain missing; event counts are zero when a trace was present.
"""

import bisect
import csv
import math
from pathlib import Path


KPI_COLUMNS = (
    'interval_s',
    'DRB.PdcpSduVolumeDL_kB', 'DRB.PdcpSduVolumeUL_kB',
    'DRB.RlcSduDelayDl_us', 'RRU.PrbTotDl', 'RRU.PrbTotUl',
    'DRB.UEThpDl_Mbps', 'DRB.UEThpUl_Mbps', 'dl_prb_symbols',
    'ul_prb_symbols', 'dl_delay_samples', 'rlc_tx_drop_dl_pdus',
    'rlc_tx_drop_ul_pdus', 'rlc_tx_drop_dl_bytes', 'rlc_tx_drop_ul_bytes',
)
PATH_COLUMNS = (
    'relay_index', 'ues_sharing_feeder', 'ues_sharing_scheduler',
    'distance_to_primary_m', 'speed_mps', 'visible', 'service_elevation_deg',
    'propagation_delay_s', 'timing_advance_s', 'ul_slot_arrival_error_ns',
    'ul_phase_direction', 'x_m', 'y_m', 'z_m',
)
TB_COLUMNS = ('tb_bytes', 'mac_bytes', 'mcs', 'rv', 'ndi', 'harq_id',
              'prbs', 'num_symbols', 'sinr_db', 'tbler', 'crc_pass_model')
TB_TOTALS = {'tb_bytes', 'mac_bytes', 'prbs', 'num_symbols'}
EVENTS = (
    'RandomAccessSuccessful', 'RandomAccessError', 'ConnectionEstablished',
    'ConnectionReconfiguration', 'HandoverEndOk', 'HandoverEndError',
    'Sib2Received', 'RadioLinkFailure', 'CRNTI_RECOVERY', 'CRNTI_MSG3',
    'UE_CTRL_TX', 'UE_CTRL_RX', 'NTN_BOUNDARY_NS', 'HARQ_GENERATED',
    'PDCP_TX', 'PDCP_RX', 'RLC_AM_TX', 'RLC_AM_RX', 'RLC_TX_DROP',
)
CONTROL_MESSAGES = ('UL_DCI', 'DL_DCI', 'DL_CQI', 'MIB', 'SIB1',
                    'RACH_PREAMBLE', 'RAR', 'BSR', 'DL_HARQ', 'SR', 'SRS')
PDU_EVENTS = ('PDCP_TX', 'PDCP_RX', 'RLC_AM_TX', 'RLC_AM_RX', 'RLC_TX_DROP')


def _features():
    names = ['kpi_age_s', *(f'kpi_{c}' for c in KPI_COLUMNS),
             'path_age_s', *(f'path_{c}' for c in PATH_COLUMNS)]
    for direction in ('dl', 'ul'):
        for event in ('tx', 'rx'):
            prefix = f'tb_{direction}_{event}_'
            names.append(prefix + 'count')
            for col in TB_COLUMNS:
                names.append(prefix + col + ('_sum' if col in TB_TOTALS else '_mean'))
        names.extend((f'harq_{direction}_ack_count', f'harq_{direction}_nack_count',
                      f'bsr_{direction}_report_count', f'bsr_{direction}_level_max',
                      f'bsr_{direction}_level_mean'))
        for event in PDU_EVENTS:
            prefix = f'event_{direction}_{event.lower()}_'
            names.extend((prefix + 'count', prefix + 'size_bytes_sum',
                          prefix + 'sequence_number_last', prefix + 'lcid_last'))
        names.extend((f'event_{direction}_ntn_boundary_count',
                      f'event_{direction}_ntn_boundary_ns_max'))
    names.extend(('event_cell_last', 'event_bwp_last', 'event_harq_process_last'))
    for event in EVENTS:
        names.append(f'event_{event.lower()}_count')
    for event in ('UE_CTRL_TX', 'UE_CTRL_RX'):
        for msg in CONTROL_MESSAGES:
            names.append(f'event_{event.lower()}_{msg.lower()}_count')
    return tuple(names)


RADIO_TOKEN_FEATURES = _features()


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _read(path):
    if not path.exists():
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def _details(value):
    return dict(part.split('=', 1) for part in value.split(';') if '=' in part)


def _mean(values):
    return sum(values) / len(values) if values else None


def _primary_rntis(kpis):
    ids = set()
    for row in kpis:
        if row.get('ue_index') != '0':
            continue
        ids.add(row.get('rnti'))
        ids.update(row.get('rnti_history', '').split('|'))
    return ids - {'', '0', None}


def _primary_imsi(events, rntis):
    for row in events:
        if row.get('rnti') in rntis and 'imsi=' in row.get('details', ''):
            return _details(row['details']).get('imsi')
    return None


def _range(rows, times, start, end):
    # Input event times are emitted in simulator order. Half-open intervals
    # make an event at a shared boundary belong to exactly one token.
    return rows[bisect.bisect_left(times, start):bisect.bisect_left(times, end)]


def _summarize_tb(result, rows):
    for direction in ('DL', 'UL'):
        for event in ('TX', 'RX'):
            matching = [r for r in rows if r['direction'] == direction and r['event'] == event]
            prefix = f'tb_{direction.lower()}_{event.lower()}_'
            result[prefix + 'count'] = float(len(matching))
            for col in TB_COLUMNS:
                values = [v for r in matching if (v := _number(r.get(col))) is not None]
                if values:
                    result[prefix + col + ('_sum' if col in TB_TOTALS else '_mean')] = (
                        sum(values) if col in TB_TOTALS else _mean(values))
                elif col in TB_TOTALS:
                    result[prefix + col + '_sum'] = 0.0


def _summarize_events(result, rows):
    for row in rows:
        event = row['event']
        direction = row.get('direction', '').lower()
        if event == 'UE_CTRL_TX':
            direction = 'ul'
        elif event == 'UE_CTRL_RX':
            direction = 'dl'
        key = f'event_{event.lower()}_count'
        if key in result:
            result[key] += 1.0
        details = _details(row.get('details', ''))
        for source, target in (('cell', 'event_cell_last'), ('bwp', 'event_bwp_last'),
                               ('process', 'event_harq_process_last')):
            value = _number(details.get(source))
            if value is not None:
                result[target] = value
        if direction not in ('dl', 'ul'):
            continue
        if event == 'HARQ_GENERATED':
            ack = _number(row.get('value'))
            if ack in (0.0, 1.0):
                result[f'harq_{direction}_{"ack" if ack else "nack"}_count'] += 1.0
        if event in PDU_EVENTS:
            prefix = f'event_{direction}_{event.lower()}_'
            result[prefix + 'count'] += 1.0
            size = _number(row.get('size_bytes'))
            if size is not None:
                result[prefix + 'size_bytes_sum'] += size
            for col in ('sequence_number', 'lcid'):
                value = _number(row.get(col))
                if value is not None and value >= 0:
                    result[prefix + col + '_last'] = value
        if event == 'NTN_BOUNDARY_NS':
            result[f'event_{direction}_ntn_boundary_count'] += 1.0
            value = _number(row.get('value'))
            key = f'event_{direction}_ntn_boundary_ns_max'
            if value is not None:
                result[key] = max(result[key], value) if result[key] is not None else value
        if event in ('UE_CTRL_TX', 'UE_CTRL_RX'):
            key = f'event_{event.lower()}_{row.get("value", "").lower()}_count'
            if key in result:
                result[key] += 1.0
            levels = details.get('bsr_levels')
            if levels is not None:
                numbers = [float(x) for x in levels.split(':') if x]
                result[f'bsr_{direction}_report_count'] += 1.0
                if numbers:
                    key = f'bsr_{direction}_level_max'
                    result[key] = max(result[key], *numbers) if result[key] is not None else max(numbers)
                    result.setdefault(f'_bsr_{direction}_levels', []).extend(numbers)
    for direction in ('dl', 'ul'):
        levels = result.pop(f'_bsr_{direction}_levels', [])
        if levels:
            result[f'bsr_{direction}_level_mean'] = _mean(levels)


def attach_radio_tokens(tokens, raw_dir: Path, prefix: str):
    """Mutate token rows with radio features and return the stable feature list."""
    required = (f'{prefix}-kpis.csv', f'{prefix}-transport-blocks.csv',
                f'{prefix}-radio-events.csv', 'ue-paths.csv')
    missing = [name for name in required if not (raw_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f'incomplete radio telemetry for {prefix}: {missing}')
    kpis = _read(raw_dir / f'{prefix}-kpis.csv')
    blocks = _read(raw_dir / f'{prefix}-transport-blocks.csv')
    events = _read(raw_dir / f'{prefix}-radio-events.csv')
    paths = _read(raw_dir / 'ue-paths.csv')
    rntis = _primary_rntis(kpis)
    imsi = _primary_imsi(events, rntis)
    kpis = sorted((r for r in kpis if r.get('ue_index') == '0'),
                  key=lambda r: float(r['time_s']))
    paths = sorted((r for r in paths if r.get('ue_index') == '0'),
                   key=lambda r: float(r['time_s']))
    blocks = sorted((r for r in blocks if r.get('rnti') in rntis),
                    key=lambda r: float(r['time_s']))
    events = sorted((r for r in events if r.get('rnti') in rntis or
                     (imsi is not None and _details(r.get('details', '')).get('imsi') == imsi)),
                    key=lambda r: float(r['time_s']))
    kpi_times = [float(r['time_s']) for r in kpis]
    path_times = [float(r['time_s']) for r in paths]
    tb_times = [float(r['time_s']) for r in blocks]
    event_times = [float(r['time_s']) for r in events]
    for token in tokens:
        start, end = float(token['t_start_s']), float(token['t_end_s'])
        values = {key: None for key in RADIO_TOKEN_FEATURES}
        if kpis:
            i = bisect.bisect_right(kpi_times, end + 1e-9) - 1
            if i >= 0:
                row = kpis[i]
                values['kpi_age_s'] = max(0.0, end - kpi_times[i])
                for col in KPI_COLUMNS:
                    values['kpi_' + col] = _number(row.get(col))
        if paths:
            i = bisect.bisect_right(path_times, end + 1e-9) - 1
            if i >= 0:
                row = paths[i]
                values['path_age_s'] = max(0.0, end - path_times[i])
                for col in PATH_COLUMNS:
                    values['path_' + col] = _number(row.get(col))
        if (raw_dir / f'{prefix}-transport-blocks.csv').exists():
            _summarize_tb(values, _range(blocks, tb_times, start, end))
        if (raw_dir / f'{prefix}-radio-events.csv').exists():
            for key in RADIO_TOKEN_FEATURES:
                if key.endswith('_count') or key.endswith('_size_bytes_sum'):
                    values[key] = 0.0
            _summarize_events(values, _range(events, event_times, start, end))
        token.update(values)
    return RADIO_TOKEN_FEATURES
