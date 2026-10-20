#ifndef RELAY_HANDOVER_H
#define RELAY_HANDOVER_H

#include "common/sim-config.h"
#include "topology/bent-pipe-channel.h"
#include "ns3/geocentric-constant-position-mobility-model.h"
#include "ns3/node-container.h"

namespace ns3
{
/** Shared geometry proxy: all channel budgets, delay and antennas follow one relay. */
class SelectedRelayMobility : public GeocentricConstantPositionMobilityModel
{
  public:
    static TypeId GetTypeId();
    void Select(Ptr<MobilityModel> satellite);
    /** Return the physical relay currently selected by this path. */
    Ptr<MobilityModel> GetSelected() const { return m_selected; }
  private:
    Vector DoGetPosition() const override;
    Vector DoGetGeocentricPosition() const override;
    Vector DoGetGeographicPosition() const override;
    Vector DoGetVelocity() const override;
    Ptr<MobilityModel> m_selected;
};

/** Same-cell, break-before-make relay switch, with explicit interruption. */
class RelayHandover : public Object
{
  public:
    static TypeId GetTypeId();
    void Configure(NodeContainer satellites, Ptr<SelectedRelayMobility> selected,
                   Ptr<MobilityModel> gnb, Ptr<MobilityModel> ue,
                   Ptr<BentPipePropagationLossModel> visibility, const ScenarioConfig& sim);
    void Start();
    /** Return the number of relay switches that have finished their interruption interval.
     */
    uint32_t Completed() const { return m_completed; }
  private:
    void Check();
    void Complete(uint32_t target);
    NodeContainer m_satellites;
    Ptr<SelectedRelayMobility> m_selected;
    Ptr<MobilityModel> m_gnb, m_ue;
    Ptr<BentPipePropagationLossModel> m_visibility;
    double m_feederMask{30}, m_serviceMask{30}, m_hysteresisM{5000};
    Time m_interval, m_trigger, m_interruption, m_candidateSince;
    uint32_t m_active{0}, m_candidate{0}, m_completed{0};
    bool m_switching{false};
};
}
#endif
