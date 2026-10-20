#include "topology/relay-handover.h"
#include "ns3/geographic-positions.h"
#include "ns3/simulator.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>

namespace ns3
{
NS_OBJECT_ENSURE_REGISTERED(SelectedRelayMobility);
NS_OBJECT_ENSURE_REGISTERED(RelayHandover);

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId SelectedRelayMobility::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SelectedRelayMobility")
        .SetParent<GeocentricConstantPositionMobilityModel>().AddConstructor<SelectedRelayMobility>();
    return tid;
}
/** Point the common mobility proxy at a new satellite and notify antenna/channel observers of the
 * geometry change.
 */
void SelectedRelayMobility::Select(Ptr<MobilityModel> satellite)
{
    m_selected = satellite;
    NotifyCourseChange();
}
/** Forward the current ECEF position from the selected satellite mobility model.
 */
Vector SelectedRelayMobility::DoGetPosition() const { return m_selected->GetPosition(); }
/** Expose the selected satellite ECEF position through the geocentric interface.
 */
Vector SelectedRelayMobility::DoGetGeocentricPosition() const { return DoGetPosition(); }
/** Convert the selected satellite live ECEF position to spherical geographic coordinates.
 */
Vector SelectedRelayMobility::DoGetGeographicPosition() const
{
    return GeographicPositions::CartesianToGeographicCoordinates(DoGetPosition(), GeographicPositions::SPHERE);
}
/** Forward the current ECEF velocity of the selected satellite.
 */
Vector SelectedRelayMobility::DoGetVelocity() const { return m_selected->GetVelocity(); }

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId RelayHandover::GetTypeId()
{
    static TypeId tid = TypeId("ns3::RelayHandover").SetParent<Object>().AddConstructor<RelayHandover>();
    return tid;
}
/** Store candidate relays, masks, hysteresis, and timers; floor interruption at the radio horizon
 * RTT plus ten milliseconds to drain old waves/grants.
 */
void RelayHandover::Configure(NodeContainer satellites, Ptr<SelectedRelayMobility> selected,
                              Ptr<MobilityModel> gnb, Ptr<MobilityModel> ue,
                              Ptr<BentPipePropagationLossModel> visibility, const ScenarioConfig& sim)
{
    m_satellites = satellites; m_selected = selected; m_gnb = gnb; m_ue = ue;
    m_visibility = visibility; m_feederMask = sim.feederMinElevationDeg;
    m_serviceMask = sim.serviceMinElevationDeg; m_hysteresisM = sim.handoverHysteresisKm * 1000;
    m_interval = MilliSeconds(sim.handoverCheckMs); m_trigger = MilliSeconds(sim.handoverTriggerMs);
    const double r = satellites.Get(0)->GetObject<MobilityModel>()->GetPosition().GetLength();
    const double horizon = std::sqrt(r*r - gnb->GetPosition().GetLength()*gnb->GetPosition().GetLength()) +
                           std::sqrt(r*r - ue->GetPosition().GetLength()*ue->GetPosition().GetLength());
    // Drain pre-switch waves and grant horizon before committing the new path.
    m_interruption = std::max(MilliSeconds(sim.handoverInterruptionMs),
                             Seconds(2*horizon/299792458.0) + MilliSeconds(10));
}
/** Run the first relay-selection check immediately; subsequent checks schedule themselves.
 */
void RelayHandover::Start() { Check(); }
/** Find the shortest path visible to both endpoints, require its path advantage for the trigger
 * duration, and gate traffic during a scheduled relay switch.
 */
void RelayHandover::Check()
{
    if (!m_switching)
    {
        uint32_t best = m_active;
        double bestRange = std::numeric_limits<double>::infinity();
        double activeRange = bestRange;
        for (uint32_t i = 0; i < m_satellites.GetN(); ++i)
        {
            auto sat = m_satellites.Get(i)->GetObject<MobilityModel>();
            const double range = m_gnb->GetDistanceFrom(sat) + m_ue->GetDistanceFrom(sat);
            if (i == m_active) activeRange = range;
            if (BentPipePropagationLossModel::ElevationDeg(m_gnb, sat) >= m_feederMask &&
                BentPipePropagationLossModel::ElevationDeg(m_ue, sat) >= m_serviceMask && range < bestRange)
            {
                best = i; bestRange = range;
            }
        }
        const bool better = best != m_active &&
                            (!m_visibility->IsVisible() || bestRange + m_hysteresisM < activeRange);
        if (!better)
        {
            m_candidate = m_active; m_candidateSince = Simulator::Now();
        }
        else
        {
            if (m_candidate != best) { m_candidate = best; m_candidateSince = Simulator::Now(); }
            if (Simulator::Now() - m_candidateSince >= m_trigger)
            {
                m_switching = true; m_visibility->SetAvailable(false);
                std::cout << "handover start timeS=" << Simulator::Now().GetSeconds()
                          << " source=" << m_active << " target=" << best
                          << " interruptionS=" << m_interruption.GetSeconds() << '\n';
                Simulator::Schedule(m_interruption, &RelayHandover::Complete, this, best);
            }
        }
    }
    Simulator::Schedule(m_interval, &RelayHandover::Check, this);
}
/** Recheck target visibility; select and count the new relay only if both hops remain covered.
 * Otherwise restore the old path. Reenable traffic and log the outcome in either case.
 */
void RelayHandover::Complete(uint32_t target)
{
    auto sat = m_satellites.Get(target)->GetObject<MobilityModel>();
    if (BentPipePropagationLossModel::ElevationDeg(m_gnb, sat) < m_feederMask ||
        BentPipePropagationLossModel::ElevationDeg(m_ue, sat) < m_serviceMask)
    {
        m_visibility->SetAvailable(true); m_switching = false; m_candidate = m_active;
        std::cout << "handover failed timeS=" << Simulator::Now().GetSeconds() << '\n';
        return;
    }
    m_selected->Select(sat); m_active = target; m_candidate = target;
    m_visibility->SetAvailable(true); m_switching = false; ++m_completed;
    std::cout << "handover complete timeS=" << Simulator::Now().GetSeconds()
              << " satellite=" << target << " count=" << m_completed << '\n';
}
}
