/*
 * NTN backhaul presets and gNB antenna tracking toward the LEO satellite.
 */

#ifndef SAT_BS_HANDSET_NTN_HELPERS_H
#define SAT_BS_HANDSET_NTN_HELPERS_H

#include "ns3/nstime.h"
#include "ns3/ptr.h"
#include "ns3/uniform-planar-array.h"

namespace ns3
{

class Node;

constexpr double kSatAltitudeMinKm = 550.0;
constexpr double kSatAltitudeMaxKm = 1200.0;

void ApplyBackhaulPreset(double& frequencyHz,
                         double& bandwidthHz,
                         double& satEIRP,
                         double& groundTxPower,
                         double& satAntennaGainDb,
                         double& vsatAntennaGainDb,
                         double& satNoiseFigureDb);

void UpdateGnbAntennaTowardSat(Ptr<Node> gnbNode,
                               Ptr<Node> satNode,
                               Ptr<UniformPlanarArray> ant,
                               Time period);

} // namespace ns3

#endif /* SAT_BS_HANDSET_NTN_HELPERS_H */
