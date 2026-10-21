"""Shared full-run configuration and satellite visibility calculations.

The planner uses these helpers to screen geometry before launching ns-3.
"""
import copy
import math

EARTH_M = 6371000.0
MU = 3.986007e14


def dot(a, b):
    """Compute the scalar product by summing the products of corresponding vector components."""
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    """Compute the three-dimensional cross product used as the orbit rotation tangent."""
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def satellite(t, radius, inclination, phase, speed):
    """Compute satellite ECEF position with Rodrigues rotation at the requested time and phase,
    including Earth rotation and the ns-3 orbit convention.
    """
    lon = -2 * math.pi * t / 86400
    inc = math.radians(inclination)
    axis = (-math.sin(inc)*math.cos(lon), -math.sin(inc)*math.sin(lon), math.cos(inc))
    initial = (radius*math.cos(inc)*math.cos(lon),
               radius*math.cos(inc)*math.sin(lon), radius*math.sin(inc))
    omega = speed / radius * (-1 if inc > math.pi / 2 else 1)
    angle = math.radians(phase) + omega * t
    tangent = cross(axis, initial)
    projection = dot(axis, initial)
    return tuple(x*math.cos(angle) + y*math.sin(angle) + z*projection*(1-math.cos(angle))
                 for x, y, z in zip(initial, tangent, axis))


def ground(latitude, altitude, longitude=0):
    """Convert latitude/longitude in degrees and altitude in metres to spherical Earth ECEF
    coordinates; longitude defaults to the reference orbital plane.
    """
    lat, lon = map(math.radians, (latitude, longitude))
    radius = EARTH_M + altitude
    return (radius*math.cos(lat)*math.cos(lon),
            radius*math.cos(lat)*math.sin(lon), radius*math.sin(lat))


def link(observer, target):
    """Subtract ECEF endpoints to return slant distance in metres and elevation in degrees
    relative to the observer radial vertical.
    """
    delta = tuple(b-a for a, b in zip(observer, target))
    distance = math.sqrt(dot(delta, delta))
    sin_elevation = dot(delta, observer) / (distance * math.sqrt(dot(observer, observer)))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation)))), distance


def preflight(cfg):
    """Sample the full orbit every 0.5 seconds, bound the UE movement disk, and follow relay
    selection; reject inadequate visibility, bootstrap budget, or switch timing.
    """
    sat, topo, ho = cfg['satellite'], cfg['topology'], cfg['handover']
    radius = EARTH_M + sat['altitudeKm']*1000
    speed = math.sqrt(MU/radius)
    lat = sat['inclinationDeg'] + topo['bsNorthOffsetKm']*1000/111320
    lon = topo['bsEastOffsetKm']*1000/(111320*math.cos(math.radians(lat)))
    bs = ground(lat, 25, lon)
    # Bound ALL positions in the UE's local movement disk, not a random path.
    # 2% cushion covers the local meters-per-degree placement approximation.
    envelope = 1.02*topo['ueDistanceM'] / (EARTH_M + 1.5)
    active = candidate = 0
    since = 0
    events = []
    min_elevation = 90
    bootstrap_cnr = math.inf
    for tick in range(math.ceil(cfg['time']['simTime']*2)+1):
        t = tick/2
        elevations, paths, ranges = [], [], []
        for i in range(sat['count']):
            pos = satellite(t, radius, sat['inclinationDeg'], -i*sat['spacingDeg'], speed)
            bs_el, feeder_range = link(bs, pos)
            gamma = math.acos(max(-1, min(1, dot(bs, pos)/(math.sqrt(dot(bs,bs))*radius))))
            worst_gamma = gamma + envelope
            ue_el = math.degrees(math.atan2(radius*math.cos(worst_gamma)-(EARTH_M+1.5),
                                           radius*math.sin(worst_gamma)))
            ue_range = math.sqrt(radius**2+(EARTH_M+1.5)**2-
                                 2*radius*(EARTH_M+1.5)*math.cos(worst_gamma))
            elevations.append((bs_el, ue_el))
            paths.append(2*feeder_range)  # Nearby-UE shortest-path ranking approximation.
            ranges.append(ue_range)
        eligible = [i for i,e in enumerate(elevations)
                    if e[0]>=sat['feederMinElevationDeg'] and e[1]>=sat['serviceMinElevationDeg']]
        best = min(eligible, key=lambda i: paths[i]) if eligible else active
        better = best != active and paths[best]+ho['hysteresisKm']*1000 < paths[active]
        if ho['enabled'] and better:
            if candidate != best: candidate, since = best, t
            if t-since >= ho['timeToTriggerMs']/1000:
                active = candidate
                events.append(t)
        else:
            candidate, since = active, t
        min_elevation = min(min_elevation, *elevations[active])
        if (elevations[active][0] < sat['feederMinElevationDeg']+5 or
                elevations[active][1] < sat['serviceMinElevationDeg']+5):
            return None
        if t <= cfg['time']['warmUp']:
            # Approximate two-RB (360 kHz at mu=0) bootstrap budget, with a
            # 2.3 dB S-band atmosphere/scintillation allowance. Not a PHY oracle.
            radio = cfg['radio']
            loss = 32.45+20*math.log10(radio['ulFrequencyHz']/1e9)+20*math.log10(ranges[active])+2.3
            noise = -174+10*math.log10(360000)+radio['satServiceNoiseFigureDb']
            cnr = radio['ueTxPower']+radio['satServiceRxGainDb']-loss-radio['serviceExcessLossDb']-noise
            bootstrap_cnr = min(bootstrap_cnr, cnr)
    if bootstrap_cnr < 2:
        return None
    if ho['enabled']:
        record_start = cfg['time']['warmUp']+cfg['time']['measureStart']
        record_end = cfg['time']['simTime']-cfg['time']['appDrain']-cfg['time']['measureEnd']
        if len(events)!=1 or not record_start+10 <= events[0] <= record_end-20:
            return None
    return {'minElevationBoundDeg': round(min_elevation,3),
            'bootstrapCnrEstimateDb': round(bootstrap_cnr,3),
            'predictedHandoverTimesS': events, 'geometryStepS': 0.5}


def draw(base, rng, workload, handover, mobile, condition, service_loss=None):
    """Copy the baseline and apply the simple campaign profile, selecting workload pacing and
    gateway offset while assigning the requested mobility, relay, and attenuation factors.
    """
    cfg = copy.deepcopy(base)
    cfg['satellite'].update(count=2, spacingDeg=8, altitudeKm=800,
                            inclinationDeg=53, antennaUpdateMs=100,
                            feederMinElevationDeg=30, serviceMinElevationDeg=30)
    cfg['handover'] = dict(enabled=handover, checkIntervalMs=100,
                           timeToTriggerMs=1000,
                           hysteresisKm=5, interruptionMs=100)
    # Equal duration avoids leaking the handover label through sequence length.
    cfg['time'].update(simTime=130, warmUp=15, appDrain=10, measureStart=5, measureEnd=1)
    cfg['topology'].update(ueDistanceM=500, receiverMobility=mobile, ueSpeedMps=1.4,
                           bsNorthOffsetKm=0, bsEastOffsetKm=rng.uniform(-20,20))
    if service_loss is None:
        service_loss = rng.choice([0,1.5,3])
    # Service attenuation moves SINR against fixed receiver noise; feeder loss is
    # a constant per-run weather-fade proxy, not a fabricated rain time series.
    feeder_loss = {'clear':0, 'moderate':12, 'degraded':24}[condition]
    dl, ul = 2.135e9, 1.945e9
    cfg['radio'].update(ueProfile='handheld', frequencyHz=dl, dlFrequencyHz=dl,
                        ulFrequencyHz=ul, feederDlFrequencyHz=dl,
                        feederUlFrequencyHz=ul, bandwidthHz=10e6,
                        dlBandwidthHz=10e6, ulBandwidthHz=10e6,
                        ueTxPower=23, ueAntennaGainDb=0, ueNoiseFigureDb=7,
                        ntnScenario='NTN-Rural', linkMarginDb=0,
                        serviceExcessLossDb=service_loss,
                        feederExcessLossDb=feeder_loss, nrTraces=False)
    app = cfg['application']
    rate_kbps = rng.choice([5,10]) if workload in ('probe','file') else 10
    app.update(workload=workload, dataRate=f'{rate_kbps}kbps', packetSize=512,
               computeDelayUs=0)
    if workload=='probe':
        app['probe'] = dict(traffic='cbr', onTime='1s', offTime='1s', burstShape=1.5)
    elif workload=='file':
        app['file'] = dict(sizeMu=math.log(16000),sizeSigma=0.3,
                           sizeMax=128000,readingMean='3s')
    elif workload=='rpc':
        app['rpc'] = dict(reqSize=128,objectScale=128,objectShape=2,
                          objectMax=1400,objectsMean=2,objectGap='0.2s',
                          readingMean='1.5s',count=0)
    elif workload=='stream':
        app['stream'] = dict(frameInterval='200ms',slicesPerFrame=2,
                             sliceScale=40,sliceShape=2,sliceMax=200,
                             sliceGapScale=0.0025,sliceGapShape=1.2,sliceGapMax=0.01)
    else:
        app['iot'] = dict(payloadMin=64,payloadMax=128,interval='1.5s')
    return cfg
