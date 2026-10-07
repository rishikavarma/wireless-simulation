/*
 * NTN bent-pipe RF presets and panel tracking toward the LEO satellite.
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

void ApplyBentPipePreset(double& frequencyHz,
                         double& bandwidthHz,
                         double& satEIRP,
                         double& ueTxPower,
                         double& ueAntennaGainDb,
                         double& gnbAntennaGainDb,
                         double& gnbNoiseFigureDb);

void UpdateAntennaToward(Ptr<Node> observer,
                         Ptr<Node> target,
                         Ptr<UniformPlanarArray> ant,
                         Time period);

} // namespace ns3

#endif /* SAT_BS_HANDSET_NTN_HELPERS_H */
