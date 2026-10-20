#include "topology/mobility.h"

#include "ns3/abort.h"
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

constexpr double kMetersPerDegree = 111320.0;

/** Install the native circular LEO mobility model with configured altitude/inclination/phase and
 * default gravity-derived orbital speed.
 */
void
InstallSatelliteMobility(Ptr<Node> satellite,
                         double satAltitudeKm,
                         double satInclinationDeg,
                         Time antennaPeriod,
                         double phaseDeg)
{
    Ptr<LeoCircularOrbitMobilityModel> satMob = CreateObject<LeoCircularOrbitMobilityModel>();
    satMob->SetAttribute("Altitude", DoubleValue(satAltitudeKm));
    satMob->SetAttribute("Inclination", DoubleValue(satInclinationDeg));
    satMob->SetAttribute("Resolution", TimeValue(antennaPeriod));
    // LeoCircularOrbitMobilityModel::DoSetPosition: x = RAAN deg, y = argument of latitude deg.
    // RAAN stays at zero; relative phase supplies the constellation spacing.
    satMob->SetPosition(Vector(0.0, phaseDeg, 0.0));
    satellite->AggregateObject(satMob);
}

/** Find the first relay initial nadir, shift it by local north/east offsets, and install a fixed
 * ECEF ground station at the requested height.
 */
void
InstallBaseStationMobility(Ptr<Node> baseStation,
                           Ptr<Node> satellite,
                           double altitudeM,
                           double northOffsetKm,
                           double eastOffsetKm)
{
    Ptr<MobilityModel> satMob = satellite->GetObject<MobilityModel>();
    NS_ABORT_MSG_IF(!satMob, "InstallSatelliteMobility must run before InstallBaseStationMobility");
    Vector geo = GeographicPositions::CartesianToGeographicCoordinates(
        satMob->GetPosition(),
        GeographicPositions::SPHERE);
    geo.z = altitudeM;
    const double northM = northOffsetKm * 1000.0;
    const double eastM = eastOffsetKm * 1000.0;
    geo.x += northM / kMetersPerDegree;
    const double cosLat = std::cos(geo.x * M_PI / 180.0);
    if (std::abs(cosLat) >= 1e-6)
    {
        geo.y += eastM / (kMetersPerDegree * cosLat);
    }
    Ptr<GeocentricEcefMobilityModel> bsMob = CreateObject<GeocentricEcefMobilityModel>();
    bsMob->SetGeographicPosition(geo);
    baseStation->AggregateObject(bsMob);
}

// Constant-speed great-circle segments between local random waypoints.
class GroundWaypointMobility : public GeocentricEcefMobilityModel
{
  public:
    /** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
     */
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::GroundWaypointMobility")
            .SetParent<GeocentricEcefMobilityModel>().AddConstructor<GroundWaypointMobility>();
        return tid;
    }
    /** Initialize the UE at the movement disk north edge, allocate the waypoint RNG, and start its
     * first constant-speed segment.
     */
    void Configure(Vector center, double radius, double speed)
    {
        m_center = center; m_radius = radius; m_speed = speed;
        m_random = CreateObject<UniformRandomVariable>();
        Vector initial(center.x + radius/kMetersPerDegree, center.y, 1.5);
        m_end = GeographicPositions::GeographicToCartesianCoordinates(
            initial.x, initial.y, initial.z, GeographicPositions::SPHERE);
        Next();
    }
  private:
    /** Draw a waypoint uniformly in the local disk, compute the great-circle angle and travel time,
     * notify observers, and schedule the next segment.
     */
    void Next()
    {
        m_start = m_end; m_departure = Simulator::Now();
        const double radius = m_radius * std::sqrt(m_random->GetValue());
        const double bearing = m_random->GetValue(0, 2*M_PI);
        const Vector geo(m_center.x + radius*std::cos(bearing)/kMetersPerDegree,
                         m_center.y + radius*std::sin(bearing)/
                             (kMetersPerDegree*std::cos(m_center.x*M_PI/180)), 1.5);
        m_end = GeographicPositions::GeographicToCartesianCoordinates(
            geo.x, geo.y, geo.z, GeographicPositions::SPHERE);
        const double r = m_start.GetLength();
        const double dot = m_start.x*m_end.x + m_start.y*m_end.y + m_start.z*m_end.z;
        // atan2 avoids acos cancellation for short ground segments on Earth-radius vectors.
        m_angle = std::atan2(CrossProduct(m_start, m_end).GetLength(), dot);
        m_seconds = std::max(0.001, r*m_angle/m_speed);
        NotifyCourseChange();
        Simulator::Schedule(Seconds(m_seconds), &GroundWaypointMobility::Next, this);
    }
    /** Interpolate the current segment with spherical linear interpolation, keeping the UE on its
     * constant-height Earth sphere.
     */
    Vector DoGetPosition() const override
    {
        if (m_angle < 1e-9) return m_start;
        const double t = std::clamp((Simulator::Now()-m_departure).GetSeconds()/m_seconds, 0.0, 1.0);
        return (m_start*std::sin((1-t)*m_angle) + m_end*std::sin(t*m_angle)) * (1.0/std::sin(m_angle));
    }
    /** Expose the same interpolated ECEF position through the geocentric mobility interface.
     */
    Vector DoGetGeocentricPosition() const override { return DoGetPosition(); }
    /** Convert the current interpolated ECEF position into spherical latitude, longitude, and
     * altitude.
     */
    Vector DoGetGeographicPosition() const override
    {
        return GeographicPositions::CartesianToGeographicCoordinates(DoGetPosition(), GeographicPositions::SPHERE);
    }
    /** Differentiate the spherical interpolation to return the current ECEF tangent velocity along
     * the segment.
     */
    Vector DoGetVelocity() const override
    {
        if (m_angle < 1e-9) return Vector();
        const double t = std::clamp((Simulator::Now()-m_departure).GetSeconds()/m_seconds, 0.0, 1.0);
        return (m_end*std::cos(t*m_angle) - m_start*std::cos((1-t)*m_angle)) *
               (m_angle/(m_seconds*std::sin(m_angle)));
    }
    Vector m_center, m_start, m_end;
    double m_radius{100}, m_speed{1.4}, m_angle{0}, m_seconds{1};
    Time m_departure;
    Ptr<UniformRandomVariable> m_random;
};
NS_OBJECT_ENSURE_REGISTERED(GroundWaypointMobility);

/** Place a fixed-height UE north of the gateway; install either fixed ECEF position or continuous
 * random-waypoint walking inside the configured disk.
 */
void
InstallUeMobility(Ptr<Node> ue,
                  Ptr<Node> baseStation,
                  double ueDistanceM,
                  bool receiverMobility,
                  double speedMps)
{
    auto bsMob = baseStation->GetObject<GeocentricEcefMobilityModel>();
    NS_ABORT_MSG_IF(!bsMob, "InstallBaseStationMobility must run before InstallUeMobility");
    const Vector bsGeo = bsMob->GetGeographicPosition();
    if (receiverMobility)
    {
        auto mobility = CreateObject<GroundWaypointMobility>();
        mobility->Configure(bsGeo, ueDistanceM, speedMps);
        ue->AggregateObject(mobility);
    }
    else
    {
        auto mobility = CreateObject<GeocentricEcefMobilityModel>();
        mobility->SetGeographicPosition(Vector(bsGeo.x + ueDistanceM/kMetersPerDegree, bsGeo.y, 1.5));
        ue->AggregateObject(mobility);
    }
}

} // namespace ns3
