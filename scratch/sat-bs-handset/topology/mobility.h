/*
 * Mobility installers for satellite, base station(s), and UE(s).
 * Call once per node (or extend to NodeContainers) when scaling the scenario.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H
#define SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H

#include "ns3/nstime.h"
#include "ns3/ptr.h"

namespace ns3
{

class Node;

void InstallSatelliteMobility(Ptr<Node> satellite,
                              double satAltitudeKm,
                              double satInclinationDeg,
                              Time antennaPeriod);

void InstallBaseStationMobility(Ptr<Node> baseStation, double latitudeDeg, double altitudeM = 25.0);

void InstallUeMobility(Ptr<Node> ue,
                       Ptr<Node> baseStation,
                       double ueDistanceM,
                       bool receiverMobility);

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_MOBILITY_H */
