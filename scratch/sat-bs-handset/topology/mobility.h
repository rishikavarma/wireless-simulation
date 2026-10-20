/*
 * Mobility installers for the LEO satellite (geometry), ground BS, and UE(s).
 * Call once per node (or extend to NodeContainers) when scaling the scenario.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H
#define SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H

#include "ns3/nstime.h"
#include "ns3/ptr.h"

namespace ns3
{

class Node;

/** Circular satellite at RAAN 0°, with configurable initial argument of latitude. */
void InstallSatelliteMobility(Ptr<Node> satellite,
                              double satAltitudeKm,
                              double satInclinationDeg,
                              Time antennaPeriod,
                              double phaseDeg = 0.0);

/**
 * Place the BS at the satellite's t=0 nadir, then shift it by the north and
 * east offsets (km). Those offsets are the geometry knob.
 */
void InstallBaseStationMobility(Ptr<Node> baseStation,
                                Ptr<Node> satellite,
                                double altitudeM,
                                double northOffsetKm,
                                double eastOffsetKm);

void InstallUeMobility(Ptr<Node> ue,
                       Ptr<Node> baseStation,
                       double ueDistanceM,
                       bool receiverMobility,
                       double speedMps = 1.4);

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H */
