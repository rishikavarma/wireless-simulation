"""Collection settings and reproducible cross-factor profiles for the full dataset."""
import copy
import hashlib
import json
import math
import random
from pathlib import Path

import campaign_config

ROOT = Path(__file__).resolve().parents[2]
FULL_SETTINGS = Path(__file__).with_name('full_dataset.json')


def calibrated_queues(config):
    """Load the measured reference and reject a model changed since calibration."""
    path = ROOT / config['queueCalibrationFile']
    reference = json.loads(path.read_text())
    if not reference['accepted']:
        raise ValueError('queue calibration was not accepted; rerun the calibration experiments')
    for name, digest in reference['modelHashes'].items():
        if name.startswith(('scratch/sat-bs-handset/', 'patches/')):
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f'queue reference is stale for {name}; recalibrate before planning')
    return dict(file=config['queueCalibrationFile'], sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bandwidthBudgets=reference['bandwidthBudgets'],
                minimumQueueBytes=reference['minimumQueueBytes'], policy=reference['budgetPolicy'])


def apply_queue_budget(cfg, config):
    """Use the same measured byte budget across traffic types and directions."""
    bandwidth = f"{cfg['radio']['ulBandwidthHz']/1e6:g}"
    reference = config['queueCalibration']
    bdp = reference['bandwidthBudgets'][bandwidth]['referenceBdpBytes']
    cfg['application']['queueSizeBytes'] = max(reference['minimumQueueBytes'],
        math.ceil(cfg['application']['queueSizeBdp'] * bdp))


def settings(preset='comparison', profile_count=None, replicates=None, design_seed=None, target_windows=None):
    if preset == 'full':
        result = json.loads(FULL_SETTINGS.read_text())
        result['queueCalibration'] = calibrated_queues(result)
    elif preset == 'comparison':
        result = dict(profileCount=50, replicates=4, rngStart=701, designSeed=11,
                      testFraction=0.25, windowStride=10, shardRuns=0)
    else:
        raise ValueError(f'unknown preset: {preset}')
    result['preset'] = preset
    for key, value in [('profileCount', profile_count), ('replicates', replicates),
                       ('designSeed', design_seed)]:
        if value is not None:
            result[key] = value
    # Enabling selection in every full profile merges baseline/relay-switch.
    result['anchorProfileCount'] = 49 if preset == 'full' else 50
    if result['profileCount'] < result['anchorProfileCount'] or (preset == 'comparison' and result['profileCount'] != 50):
        raise ValueError('comparison has 50 profiles; full requires at least 49 anchor profiles')
    if preset == 'full' and result.get('relaySwitchEnabled') is not True:
        raise ValueError('the full preset requires relaySwitchEnabled: true')
    if preset == 'full' and result.get('satelliteAltitudeKm') != 800:
        raise ValueError('the full preset fixes satelliteAltitudeKm at 800')
    if result['replicates'] < 2:
        raise ValueError('at least two RNG replicates are required for a held-out split')
    if result['designSeed'] < 0:
        raise ValueError('design seed must be nonnegative')
    if not 0 < result['testFraction'] < 1:
        raise ValueError('testFraction must be between zero and one')
    if preset == 'full' and any(result[key] <= 0 for key in ('shardRuns', 'quantileSampleTokens')):
        raise ValueError('full-preset shard/sample sizes must be positive')
    if preset == 'full' and result['windowStride'] < 20:
        raise ValueError('full-preset windows must not overlap: windowStride must be at least 20')
    n_test = max(1, math.ceil(result['replicates'] * result['testFraction']))
    n_test = min(result['replicates'] - 1, n_test)
    result['testReplicates'] = list(range(result['replicates'] - n_test, result['replicates']))
    if target_windows is not None:
        if preset != 'full' or target_windows < 1:
            raise ValueError('--target-windows requires the full preset and a positive target')
        windows_per_run = (990 - 20) // result['windowStride'] + 1
        result['profileCount'] = max(result['profileCount'], math.ceil(target_windows / (windows_per_run * result['replicates'])))
        result['targetWindows'] = target_windows
    return result


def test_replicates(manifest):
    """Older 200-run manifests hold out replicate 3."""
    return set(manifest.get('settings', {}).get('testReplicates', [3]))


def preflight(cfg):
    """Check the primary disk and the conservative envelope of stationary background UEs."""
    envelope = copy.deepcopy(cfg)
    if cfg.get('background', {}).get('count', 0):
        envelope['topology']['ueDistanceM'] += cfg['background']['distanceM']
    return campaign_config.preflight(envelope)


def expand_profiles(anchors, config):
    """Keep controlled comparisons and add balanced application/transport/direction strata.

    The joint environment is sampled within each stratum, rather than expanding
    an impractically large Cartesian product. Every accepted profile gets the
    same independent RNG replicate set at execution time.
    """
    if config['preset'] == 'comparison':
        return anchors, 0
    enabled_anchors = []
    signatures = set()
    for name, group, original in anchors:
        cfg = copy.deepcopy(original)
        cfg['handover']['enabled'] = True
        cfg['satellite']['altitudeKm'] = config['satelliteAltitudeKm']
        apply_queue_budget(cfg, config)
        signature = json.dumps(cfg, sort_keys=True)
        if signature not in signatures:
            enabled_anchors.append((name, group, cfg))
            signatures.add(signature)
    anchors = enabled_anchors
    if len(anchors) != config['anchorProfileCount']:
        raise ValueError('full-preset anchor count changed; review profile deduplication')
    templates = {cfg['application']['workload']: cfg for _, group, cfg in anchors
                 if group in ('baseline', 'application')}
    strata = []
    for app in config['applications']:
        if app not in templates:
            raise ValueError(f'unsupported application: {app}')
        for transport, variant in [('tcp', x) for x in config['tcpVariants']] + [('udp', 'TcpNewReno')]:
            for direction in (('ul', 'dl') if app in ('voip', 'iperf', 'cbr', 'onoff') else ('ul',)):
                strata.append((app, transport, variant, direction))
    rng = random.Random(config['designSeed'])
    rng.shuffle(strata)
    ranges = config['ranges']
    choose = lambda key: rng.choice(ranges[key])
    result = list(anchors)
    signatures = {json.dumps(cfg, sort_keys=True) for _, _, cfg in result}
    rejected = 0
    while len(result) < config['profileCount']:
        app, transport, variant, direction = strata[(len(result) - len(anchors)) % len(strata)]
        cfg = copy.deepcopy(templates[app])
        application = cfg['application']
        application.update(transport=transport, tcpVariant=variant, direction=direction,
                           queueSizeBdp=choose('queueSizeBdp'))
        if app in ('probe', 'file', 'iperf', 'cbr', 'onoff'):
            application['packetSize'] = choose('packetBytes')
        if app in ('probe', 'file', 'cbr', 'onoff') or (app == 'iperf' and transport == 'udp'):
            application['dataRate'] = f"{choose('sourceRateKbps'):g}kbps"
        if app == 'probe':
            application['probe']['traffic'] = choose('probeTraffic')
        elif app == 'file':
            median = choose('fileMedianBytes')
            application['file'].update(sizeMu=math.log(median), sizeMax=8 * median,
                                       readingMean=f"{choose('fileReadingMeanS'):g}s")
        elif app == 'rpc':
            application['rpc'].update(reqSize=choose('rpcRequestBytes'),
                objectScale=choose('rpcObjectScaleBytes'), readingMean=f"{choose('rpcReadingMeanS'):g}s")
        elif app == 'stream':
            application['stream'].update(frameInterval=f"{1/choose('streamFramesPerSecond'):.12g}s",
                slicesPerFrame=choose('streamSlicesPerFrame'), sliceScale=choose('streamSliceScaleBytes'),
                sliceMax=1000)
        elif app == 'iot':
            low = choose('iotPayloadMinBytes')
            application['iot'].update(payloadMin=low, payloadMax=2 * low,
                                      interval=f"{choose('iotIntervalS'):g}s")
        speed = choose('ueSpeedMps')
        cfg['topology'].update(receiverMobility=speed > 0, ueSpeedMps=speed or 1.4,
            ueDistanceM=choose('ueDiskRadiusM'), bsNorthOffsetKm=choose('gatewayOffsetKm'),
            bsEastOffsetKm=choose('gatewayOffsetKm'))
        bandwidth = choose('bandwidthMHz') * 1e6
        cfg['radio'].update(bandwidthHz=bandwidth, dlBandwidthHz=bandwidth, ulBandwidthHz=bandwidth,
            serviceExcessLossDb=choose('serviceExcessLossDb'), feederExcessLossDb=choose('feederExcessLossDb'))
        cfg['handover']['enabled'] = True
        apply_queue_budget(cfg, config)
        count = choose('backgroundCount')
        cfg['background'] = dict(count=count, relay=choose('backgroundRelay') if count else 0,
            dataRate=f"{choose('backgroundRateKbps'):g}kbps" if count else '2kbps',
            distanceM=choose('backgroundDistanceM') if count else 500)
        signature = json.dumps(cfg, sort_keys=True)
        if signature in signatures or not preflight(cfg):
            rejected += 1
            if rejected > config['profileCount'] * 100:
                raise ValueError('cannot admit enough unique profiles; review full_dataset.json ranges')
            continue
        signatures.add(signature)
        name = f'mix-{len(result)-len(anchors):04d}-{app}-{transport}-{variant.removeprefix("Tcp")}-{direction}'
        result.append((name, 'interaction', cfg))
    return result, rejected


def planned_size(config):
    runs = config['profileCount'] * config['replicates']
    # Shared fixed 100 ms bins cover 99 seconds; context and future are 10 tokens each.
    windows_per_run = (990 - 20) // config['windowStride'] + 1
    return dict(runs=runs, uniqueTimeTokens=runs * 990, tokensPerRun=990,
                windowsPerRun=windows_per_run, windows=runs * windows_per_run,
                trainRuns=config['profileCount']*(config['replicates']-len(config['testReplicates'])),
                testRuns=config['profileCount']*len(config['testReplicates']),
                windowStride=config['windowStride'], context=10, future=10,
                tokenSeconds=0.1, expectedOnly=True)
