/*
 * Transparent bent-pipe between a ground gNB and a ground UE.
 * The satellite is a transparent relay. Path loss is the feeder hop plus
 * the service hop, with a fixed transponder gain. The one-way light time
 * is the NR channel delay. The handset slot clock starts that much later,
 * so a downlink burst arrives as the handset's slot begins.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H
#define SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H

#include "ns3/phased-array-spectrum-propagation-loss-model.h"
#include "ns3/propagation-delay-model.h"
#include "ns3/propagation-loss-model.h"

namespace ns3
{

class MobilityModel;
class ThreeGppPropagationLossModel;

class BentPipePropagationLossModel : public PropagationLossModel
{
  public:
    static TypeId GetTypeId();

    /**
     * One NTN hop model, evaluated as ground↔satellite. relayGainDb is the
     * fixed transponder gain, chosen so the satellite's forward EIRP matches
     * the configured value at installation.
     */
    void Configure(Ptr<MobilityModel> gnb,
                   Ptr<MobilityModel> ue,
                   Ptr<MobilityModel> satellite,
                   Ptr<ThreeGppPropagationLossModel> hopLoss,
                   double gnbGainDb,
                   double ueGainDb,
                   double relayGainDb);

    double HopLossDb(Ptr<MobilityModel> a, Ptr<MobilityModel> b) const;

  private:
    double DoCalcRxPower(double txPowerDbm,
                         Ptr<MobilityModel> a,
                         Ptr<MobilityModel> b) const override;
    int64_t DoAssignStreams(int64_t stream) override;

    double GainDb(Ptr<MobilityModel> mob) const;

    Ptr<MobilityModel> m_gnb;
    Ptr<MobilityModel> m_ue;
    Ptr<MobilityModel> m_satellite;
    Ptr<ThreeGppPropagationLossModel> m_hopLoss;
    double m_gnbGainDb{0.0};
    double m_ueGainDb{0.0};
    double m_relayGainDb{0.0};
};

class BentPipeDelayModel : public PropagationDelayModel
{
  public:
    static TypeId GetTypeId();

    void SetDelay(Time delay);

    Time GetDelay(Ptr<MobilityModel> a, Ptr<MobilityModel> b) const override;

  private:
    int64_t DoAssignStreams(int64_t stream) override;

    Time m_delay;
};

/**
 * Sits behind the NR fading model and keeps the scalar bent-pipe budget.
 * The installed fading model only sees the two ground endpoints.
 */
class BentPipeFlatSpectrumModel : public PhasedArraySpectrumPropagationLossModel
{
  public:
    static TypeId GetTypeId();

  private:
    Ptr<SpectrumSignalParameters> DoCalcRxPowerSpectralDensity(
        Ptr<const SpectrumSignalParameters> params,
        Ptr<const MobilityModel> a,
        Ptr<const MobilityModel> b,
        Ptr<const PhasedArrayModel> aPhasedArrayModel,
        Ptr<const PhasedArrayModel> bPhasedArrayModel) const override;
    int64_t DoAssignStreams(int64_t stream) override;
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H */
