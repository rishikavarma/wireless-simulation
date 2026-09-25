#include "common/ntn-helpers.h"

#include "ns3/angles.h"
#include "ns3/geographic-positions.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/simulator.h"

namespace ns3
{

void
ApplyBackhaulPreset(double& frequencyHz,
                    double& bandwidthHz,
                    double& satEIRP,
                    double& groundTxPower,
                    double& satAntennaGainDb,
                    double& vsatAntennaGainDb,
                    double& satNoiseFigureDb)
{
    frequencyHz = 20e9;
    bandwidthHz = 400e6;
    satEIRP = 20;
    groundTxPower = 40;
    satAntennaGainDb = 38.5;
    vsatAntennaGainDb = 50;
    satNoiseFigureDb = 5.0;
}

void
UpdateGnbAntennaTowardSat(Ptr<Node> gnbNode,
                          Ptr<Node> satNode,
                          Ptr<UniformPlanarArray> ant,
                          Time period)
{
    const Vector satEcef = satNode->GetObject<MobilityModel>()->GetPosition();
    const Vector gnbEcef = gnbNode->GetObject<MobilityModel>()->GetPosition();
    const Vector gnbGeo =
        GeographicPositions::CartesianToGeographicCoordinates(gnbEcef, GeographicPositions::SPHERE);
    const Vector satGeo =
        GeographicPositions::CartesianToGeographicCoordinates(satEcef, GeographicPositions::SPHERE);
    const Vector enu =
        GeographicPositions::GeographicToTopocentricCoordinates(satGeo,
                                                                gnbGeo,
                                                                GeographicPositions::SPHERE);
    const Angles angles(enu);
    ant->SetAlpha(angles.GetAzimuth());
    ant->SetBeta(angles.GetInclination());
    Simulator::Schedule(period, &UpdateGnbAntennaTowardSat, gnbNode, satNode, ant, period);
}

} // namespace ns3
