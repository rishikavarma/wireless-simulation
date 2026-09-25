#include "topology/mobility.h"

#include "ns3/double.h"
#include "ns3/geocentric-ecef-mobility-model.h"
#include "ns3/hierarchical-mobility-model.h"
#include "ns3/leo-circular-orbit-mobility-model.h"
#include "ns3/mobility-module.h"
#include "ns3/string.h"

#include <algorithm>

namespace ns3
{

void
InstallSatelliteMobility(Ptr<Node> satellite,
                         double satAltitudeKm,
                         double satInclinationDeg,
                         Time antennaPeriod)
{
    Ptr<LeoCircularOrbitMobilityModel> satMob = CreateObject<LeoCircularOrbitMobilityModel>();
    satMob->SetAttribute("Altitude", DoubleValue(satAltitudeKm));
    satMob->SetAttribute("Inclination", DoubleValue(satInclinationDeg));
    satMob->SetAttribute("Resolution", TimeValue(antennaPeriod));
    satMob->SetPosition(Vector(0.0, 0.0, 0.0));
    satellite->AggregateObject(satMob);
}

void
InstallBaseStationMobility(Ptr<Node> baseStation, double latitudeDeg, double altitudeM)
{
    Ptr<GeocentricEcefMobilityModel> bsMob = CreateObject<GeocentricEcefMobilityModel>();
    bsMob->SetGeographicPosition(Vector(latitudeDeg, 0.0, altitudeM));
    baseStation->AggregateObject(bsMob);
}

void
InstallUeMobility(Ptr<Node> ue,
                  Ptr<Node> baseStation,
                  double ueDistanceM,
                  bool receiverMobility)
{
    Ptr<GeocentricEcefMobilityModel> bsMob = baseStation->GetObject<GeocentricEcefMobilityModel>();
    NS_ABORT_MSG_IF(!bsMob, "InstallBaseStationMobility must run before InstallUeMobility");

    Ptr<MobilityModel> ueChild;
    if (receiverMobility)
    {
        const double half = std::max(10.0, ueDistanceM);
        Ptr<RandomWalk2dMobilityModel> walk = CreateObject<RandomWalk2dMobilityModel>();
        walk->SetAttribute("Bounds", RectangleValue(Rectangle(-half, half, -half, half)));
        walk->SetAttribute("Speed", StringValue("ns3::UniformRandomVariable[Min=1.0|Max=2.0]"));
        walk->SetPosition(Vector(ueDistanceM, 0.0, 0.0));
        ueChild = walk;
    }
    else
    {
        Ptr<ConstantPositionMobilityModel> fixed = CreateObject<ConstantPositionMobilityModel>();
        fixed->SetPosition(Vector(ueDistanceM, 0.0, 0.0));
        ueChild = fixed;
    }

    Ptr<HierarchicalMobilityModel> ueMob = CreateObject<HierarchicalMobilityModel>();
    ueMob->SetParent(bsMob);
    ueMob->SetChild(ueChild);
    ue->AggregateObject(ueMob);
}

} // namespace ns3
