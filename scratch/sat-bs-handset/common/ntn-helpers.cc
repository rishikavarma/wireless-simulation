#include "common/ntn-helpers.h"

#include "ns3/angles.h"
#include "ns3/geographic-positions.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/simulator.h"

namespace ns3
{

/** Convert the target ECEF position to the observer local topocentric frame, point the array at its
 * azimuth/inclination, and schedule the next update.
 */
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
