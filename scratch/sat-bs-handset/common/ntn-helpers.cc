#include "common/ntn-helpers.h"

#include "ns3/angles.h"
#include "ns3/geographic-positions.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/simulator.h"

namespace ns3
{

void
ApplyBentPipePreset(double& frequencyHz,
                    double& bandwidthHz,
                    double& satEIRP,
                    double& ueTxPower,
                    double& ueAntennaGainDb,
                    double& gnbAntennaGainDb,
                    double& gnbNoiseFigureDb)
{
    frequencyHz = 20e9;
    bandwidthHz = 400e6;
    satEIRP = 20;
    ueTxPower = 40;
    ueAntennaGainDb = 38.5;
    gnbAntennaGainDb = 50;
    gnbNoiseFigureDb = 5.0;
}

void
UpdateAntennaToward(Ptr<Node> observer,
                    Ptr<Node> target,
                    Ptr<UniformPlanarArray> ant,
                    Time period)
{
    const Vector observerEcef = observer->GetObject<MobilityModel>()->GetPosition();
    const Vector targetEcef = target->GetObject<MobilityModel>()->GetPosition();
    const Vector observerGeo = GeographicPositions::CartesianToGeographicCoordinates(
        observerEcef,
        GeographicPositions::SPHERE);
    const Vector targetGeo = GeographicPositions::CartesianToGeographicCoordinates(
        targetEcef,
        GeographicPositions::SPHERE);
    const Vector enu = GeographicPositions::GeographicToTopocentricCoordinates(
        targetGeo,
        observerGeo,
        GeographicPositions::SPHERE);
    const Angles angles(enu);
    ant->SetAlpha(angles.GetAzimuth());
    ant->SetBeta(angles.GetInclination());
    Simulator::Schedule(period, &UpdateAntennaToward, observer, target, ant, period);
}

} // namespace ns3
