"""Controlled sweeps of every full-dataset candidate range, plus protocol choices."""
import copy
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts/dataset'))
import campaign_config
import dataset_presets
from run_dataset import profiles


def sweeps(settings, calibration):
    candidates = json.loads((ROOT / 'scripts/dataset/full_dataset.json').read_text())
    ranges = candidates['ranges']
    templates = {cfg['application']['workload']: copy.deepcopy(cfg)
                 for _, group, cfg in profiles() if group in ('baseline', 'application')}

    def base(app='cbr', background=False):
        cfg = copy.deepcopy(templates[app])
        cfg['handover']['enabled'] = settings['backgroundRelaySwitchEnabled'] if background else True
        cfg['application'].update(dataRate=f"{settings['referenceSourceRateKbps']}kbps",
                                  queueSizeBdp=2)
        cfg['metrics'] = dict(physical=True, payload=False, intervalS=1)
        cfg['background'] = dict(count=settings['backgroundReferenceCount'] if background else 0,
                                 dataRate=f"{settings['backgroundReferenceRateKbps']}kbps",
                                 relay=0, distanceM=500)
        return cfg

    result = []

    def add(key, title, values, context, setter, reference, note=''):
        levels = []
        for value in values:
            cfg = copy.deepcopy(context)
            setter(cfg, value)
            # Queue size is independent of pacing; every variant uses this run's
            # current clear-link calibration, including bandwidth-dependent budgets.
            bandwidth = f"{cfg['radio']['ulBandwidthHz']/1e6:g}"
            bdp = calibration['bandwidthBudgets'][bandwidth]['referenceBdpBytes']
            cfg['application']['queueSizeBytes'] = max(calibration['minimumQueueBytes'],
                math.ceil(cfg['application']['queueSizeBdp'] * bdp))
            cfg['output']['prefix'] = ''
            proof = dataset_presets.preflight(cfg)
            if not proof:
                raise ValueError(f'{key}={value}: geometry/bootstrap admission failed')
            levels.append(dict(value=value, label=str(value), config=cfg, preflight=proof))
        if reference not in values:
            raise ValueError(f'{key}: missing reference level')
        result.append(dict(key=key, title=title, reference=values.index(reference),
                           note=note, levels=levels))

    def assign(section, field):
        return lambda cfg, value: cfg[section].__setitem__(field, value)

    def nested(app, field):
        return lambda cfg, value: cfg['application'][app].__setitem__(field, value)

    add('application', 'Application workload', candidates['applications'], base(),
        lambda cfg, value: cfg['application'].update(copy.deepcopy(templates[value]['application']),
            dataRate=f"{settings['referenceSourceRateKbps']}kbps", queueSizeBdp=2), 'cbr',
        'Application timing and framing change together; source rate only paces supported workloads.')
    add('transport', 'TCP versus UDP', ['tcp', 'udp'], base(),
        assign('application', 'transport'), 'tcp', 'Matched native CBR sources. TCP metrics are unavailable for UDP.')
    add('tcpVariant', 'TCP congestion control (backlogged bulk)', candidates['tcpVariants'],
        base('iperf'), assign('application', 'tcpVariant'), 'TcpNewReno')
    add('direction', 'Primary data direction (native CBR)', ['ul', 'dl'], base(),
        assign('application', 'direction'), 'ul')
    add('relaySwitch', 'Automatic relay switching', [False, True], base(),
        assign('handover', 'enabled'), True, 'Disabled control retains the same two-satellite geometry.')
    add('sourceRateKbps', 'CBR source rate (kbit/s)', ranges['sourceRateKbps'], base(),
        lambda cfg, value: cfg['application'].update(dataRate=f'{value:g}kbps'), 32)
    add('packetBytes', 'CBR packet size (bytes)', ranges['packetBytes'], base(),
        assign('application', 'packetSize'), 512)
    add('queueSizeBdp', 'Queue budget (multiples of calibrated BDP)', ranges['queueSizeBdp'],
        base('iperf'), assign('application', 'queueSizeBdp'), 2,
        'Backlogged TCP bulk exposes buffering; 10 KiB floor can make low factors identical.')
    context = base()
    context['topology']['ueDistanceM'] = 50000
    add('ueSpeedMps', 'UE speed (m/s), 50 km disk', ranges['ueSpeedMps'], context,
        lambda cfg, value: cfg['topology'].update(receiverMobility=value > 0, ueSpeedMps=value or 1.4), 0,
        'Geometry changes only: this model does not add Doppler or fast fading with speed.')
    context = base()
    context['topology'].update(receiverMobility=True, ueSpeedMps=15)
    add('ueDiskRadiusM', 'UE disk radius (m), speed 15 m/s', ranges['ueDiskRadiusM'], context,
        assign('topology', 'ueDistanceM'), 500,
        'Radius changes initial placement as well as the movement boundary.')
    for axis in ('North', 'East'):
        add('gateway'+axis+'OffsetKm', f'Gateway {axis.lower()} offset (km)', ranges['gatewayOffsetKm'],
            base(), assign('topology', 'bs'+axis+'OffsetKm'), 0)
    add('bandwidthMHz', 'Paired service bandwidth (MHz)', ranges['bandwidthMHz'], base(),
        lambda cfg, value: cfg['radio'].update(bandwidthHz=value*1e6,
            dlBandwidthHz=value*1e6, ulBandwidthHz=value*1e6), 10,
        'UL/DL bandwidths move together; queue bytes follow the bandwidth-specific calibrated BDP.')
    for key, title in [('serviceExcessLossDb', 'Service excess attenuation (dB)'),
                       ('feederExcessLossDb', 'Feeder excess attenuation (dB)')]:
        add(key, title, ranges[key], base(), assign('radio', key), 0)
    for key, field, title, reference in [
        ('backgroundCount', 'count', 'Background UE count', 3),
        ('backgroundRateKbps', 'dataRate', 'Rate per background UE (kbit/s)', 32),
        ('backgroundDistanceM', 'distanceM', 'Background separation (m)', 500),
        ('backgroundRelay', 'relay', 'Background relay index', 0)]:
        setter = (lambda cfg, value: cfg['background'].update(dataRate=f'{value:g}kbps')) if field == 'dataRate' else assign('background', field)
        values = sorted(set(ranges[key]) | {5, 20, 80}) if key == 'backgroundRateKbps' else ranges[key]
        add(key, title, values, base(background=True), setter, reference,
            'Other background settings fixed at 3 UEs, 32 kbit/s each, 500 m, relay 0; only the named factor changes. '
            'Relay switching is disabled for these controls: concurrent switching/background traffic can abort the NR PHY.')
    add('probeTraffic', 'Probe traffic pattern', ranges['probeTraffic'], base('probe'),
        nested('probe', 'traffic'), 'cbr')
    add('fileMedianBytes', 'Median file content size (bytes)', ranges['fileMedianBytes'], base('file'),
        lambda cfg, value: cfg['application']['file'].update(sizeMu=math.log(value), sizeMax=8*value), 16000)
    for key, app, field, title, reference in [
        ('fileReadingMeanS', 'file', 'readingMean', 'Mean file reading pause (s)', 3),
        ('rpcRequestBytes', 'rpc', 'reqSize', 'RPC request size (bytes)', 128),
        ('rpcObjectScaleBytes', 'rpc', 'objectScale', 'RPC response-object scale (bytes)', 128),
        ('rpcReadingMeanS', 'rpc', 'readingMean', 'Mean RPC reading pause (s)', 1.5),
        ('streamFramesPerSecond', 'stream', 'frameInterval', 'Stream frame rate (frames/s)', 5),
        ('streamSlicesPerFrame', 'stream', 'slicesPerFrame', 'Stream slices per frame', 2),
        ('streamSliceScaleBytes', 'stream', 'sliceScale', 'Stream slice scale (bytes)', 40),
        ('iotPayloadMinBytes', 'iot', 'payloadMin', 'IoT minimum payload (bytes)', 64),
        ('iotIntervalS', 'iot', 'interval', 'Mean IoT reporting interval (s)', 1.5)]:
        def setter(cfg, value, app=app, field=field):
            if field in ('readingMean', 'interval'):
                value = f'{value:g}s'
            elif field == 'frameInterval':
                value = f'{1/value:.12g}s'
            cfg['application'][app][field] = value
            if field == 'payloadMin':
                cfg['application'][app]['payloadMax'] = 2*value
        context = base(app)
        if app == 'stream':
            context['application']['stream']['sliceMax'] = 1000
        add(key, title, ranges[key], context, setter, reference)
    covered = {g['key'] for g in result}
    assert set(ranges) - {'gatewayOffsetKm'} <= covered
    return result
