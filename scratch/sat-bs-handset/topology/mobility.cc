#include "topology/mobility.h"

#include "ns3/double.h"
#include "ns3/geocentric-ecef-mobility-model.h"
#include "ns3/geographic-positions.h"
#include "ns3/leo-circular-orbit-mobility-model.h"
#include "ns3/mobility-module.h"
#include "ns3/random-variable-stream.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <cmath>

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
InstallBaseStationMobility(Ptr<Node> baseStation, Ptr<Node> satellite, double altitudeM)
{
    Ptr<MobilityModel> satMob = satellite->GetObject<MobilityModel>();
    NS_ABORT_MSG_IF(!satMob, "InstallSatelliteMobility must run before InstallBaseStationMobility");
    // Both ground endpoints sit at the satellite's initial nadir, inside the
    // footprint. The orbit starts at the ascending node, so a latitude equal
    // to the inclination is below the horizon for the whole short run.
    const Vector satGeo = GeographicPositions::CartesianToGeographicCoordinates(
        satMob->GetPosition(),
        GeographicPositions::SPHERE);
    Ptr<GeocentricEcefMobilityModel> bsMob = CreateObject<GeocentricEcefMobilityModel>();
    bsMob->SetGeographicPosition(Vector(satGeo.x, satGeo.y, altitudeM));
    baseStation->AggregateObject(bsMob);
}

constexpr double kMetersPerDegree = 111320.0;

void
StepUeWalk(Ptr<GeocentricEcefMobilityModel> ueMob, Vector originGeo, double radiusM)
{
    const double half = std::max(10.0, radiusM);
    Ptr<UniformRandomVariable> rv = CreateObject<UniformRandomVariable>();
    const double north = rv->GetValue(-half, half);
    const double east = rv->GetValue(-half, half);
    const double dLat = north / kMetersPerDegree;
    const double cosLat = std::cos(originGeo.x * M_PI / 180.0);
    const double dLon = (std::abs(cosLat) < 1e-6) ? 0.0 : east / (kMetersPerDegree * cosLat);
    ueMob->SetGeographicPosition(Vector(originGeo.x + dLat, originGeo.y + dLon, originGeo.z));
    Simulator::Schedule(Seconds(1.0), &StepUeWalk, ueMob, originGeo, radiusM);
}

void
InstallUeMobility(Ptr<Node> ue,
                  Ptr<Node> baseStation,
                  double ueDistanceM,
                  bool receiverMobility)
{
    Ptr<GeocentricEcefMobilityModel> bsMob = baseStation->GetObject<GeocentricEcefMobilityModel>();
    NS_ABORT_MSG_IF(!bsMob, "InstallBaseStationMobility must run before InstallUeMobility");

    const Vector bsGeo = bsMob->GetGeographicPosition();
    Ptr<GeocentricEcefMobilityModel> ueMob = CreateObject<GeocentricEcefMobilityModel>();
    ueMob->SetGeographicPosition(
        Vector(bsGeo.x + (ueDistanceM / kMetersPerDegree), bsGeo.y, bsGeo.z));
    ue->AggregateObject(ueMob);
    if (receiverMobility)
    {
        Simulator::Schedule(Seconds(1.0), &StepUeWalk, ueMob, bsGeo, ueDistanceM);
    }
}

} // namespace ns3
