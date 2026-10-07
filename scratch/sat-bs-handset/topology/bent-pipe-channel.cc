#include "topology/bent-pipe-channel.h"

#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/spectrum-signal-parameters.h"
#include "ns3/three-gpp-propagation-loss-model.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("BentPipeChannel");

NS_OBJECT_ENSURE_REGISTERED(BentPipePropagationLossModel);
NS_OBJECT_ENSURE_REGISTERED(BentPipeDelayModel);
NS_OBJECT_ENSURE_REGISTERED(BentPipeFlatSpectrumModel);

TypeId
BentPipePropagationLossModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipePropagationLossModel")
                            .SetParent<PropagationLossModel>()
                            .SetGroupName("Propagation")
                            .AddConstructor<BentPipePropagationLossModel>();
    return tid;
}

void
BentPipePropagationLossModel::Configure(Ptr<MobilityModel> gnb,
                                        Ptr<MobilityModel> ue,
                                        Ptr<MobilityModel> satellite,
                                        Ptr<ThreeGppPropagationLossModel> hopLoss,
                                        double gnbGainDb,
                                        double ueGainDb,
                                        double relayGainDb)
{
    m_gnb = gnb;
    m_ue = ue;
    m_satellite = satellite;
    m_hopLoss = hopLoss;
    m_gnbGainDb = gnbGainDb;
    m_ueGainDb = ueGainDb;
    m_relayGainDb = relayGainDb;
}

double
BentPipePropagationLossModel::HopLossDb(Ptr<MobilityModel> a, Ptr<MobilityModel> b) const
{
    return -m_hopLoss->CalcRxPower(0.0, a, b);
}

double
BentPipePropagationLossModel::GainDb(Ptr<MobilityModel> mob) const
{
    if (mob->GetObject<Node>() == m_gnb->GetObject<Node>())
    {
        return m_gnbGainDb;
    }
    return m_ueGainDb;
}

double
BentPipePropagationLossModel::DoCalcRxPower(double txPowerDbm,
                                            Ptr<MobilityModel> a,
                                            Ptr<MobilityModel> b) const
{
    const double feederAndServiceDb =
        HopLossDb(a, m_satellite) + HopLossDb(m_satellite, b);
    return txPowerDbm - feederAndServiceDb + m_relayGainDb + GainDb(a) + GainDb(b);
}

int64_t
BentPipePropagationLossModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

TypeId
BentPipeDelayModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipeDelayModel")
                            .SetParent<PropagationDelayModel>()
                            .SetGroupName("Propagation")
                            .AddConstructor<BentPipeDelayModel>();
    return tid;
}

void
BentPipeDelayModel::SetDelay(Time delay)
{
    m_delay = delay;
}

Time
BentPipeDelayModel::GetDelay(Ptr<MobilityModel> /*a*/, Ptr<MobilityModel> /*b*/) const
{
    return m_delay;
}

int64_t
BentPipeDelayModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

TypeId
BentPipeFlatSpectrumModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipeFlatSpectrumModel")
                            .SetParent<PhasedArraySpectrumPropagationLossModel>()
                            .SetGroupName("Spectrum")
                            .AddConstructor<BentPipeFlatSpectrumModel>();
    return tid;
}

Ptr<SpectrumSignalParameters>
BentPipeFlatSpectrumModel::DoCalcRxPowerSpectralDensity(
    Ptr<const SpectrumSignalParameters> params,
    Ptr<const MobilityModel> /*a*/,
    Ptr<const MobilityModel> /*b*/,
    Ptr<const PhasedArrayModel> /*aPhasedArrayModel*/,
    Ptr<const PhasedArrayModel> /*bPhasedArrayModel*/) const
{
    return params->Copy();
}

int64_t
BentPipeFlatSpectrumModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

} // namespace ns3
