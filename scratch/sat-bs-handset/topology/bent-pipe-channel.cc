#include "topology/bent-pipe-channel.h"

#include "ns3/abort.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/spectrum-value.h"
#include "ns3/spectrum-phy.h"

#include <algorithm>
#include <cmath>
#include "ns3/spectrum-signal-parameters.h"
#include "ns3/three-gpp-propagation-loss-model.h"

namespace ns3
{

namespace
{
constexpr double kSpeedOfLightMps = 299792458.0;
}

NS_LOG_COMPONENT_DEFINE("BentPipeChannel");

NS_OBJECT_ENSURE_REGISTERED(BentPipePropagationLossModel);
NS_OBJECT_ENSURE_REGISTERED(BentPipeDelayModel);
NS_OBJECT_ENSURE_REGISTERED(BentPipeSpectrumModel);
NS_OBJECT_ENSURE_REGISTERED(BentPipeVisibilityFilter);

void BentPipePropagationLossModel::RegisterPath(Ptr<const MobilityModel> ue, Ptr<BentPipePropagationLossModel> path) {
    m_paths[ue]=path;
}
const BentPipePropagationLossModel* BentPipePropagationLossModel::Resolve(Ptr<const MobilityModel> a, Ptr<const MobilityModel> b) const {
    for(auto endpoint:{a,b}) {auto it=m_paths.find(endpoint);if(it!=m_paths.end()) return PeekPointer(it->second);}
    return this;
}

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
BentPipePropagationLossModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipePropagationLossModel")
                            .SetParent<PropagationLossModel>()
                            .SetGroupName("Propagation")
                            .AddConstructor<BentPipePropagationLossModel>();
    return tid;
}

/** Store the ground endpoints, selected-relay proxy, and validated elevation masks for the common
 * visibility gate.
 */
void
BentPipePropagationLossModel::Configure(Ptr<MobilityModel> gnb,
                                        Ptr<MobilityModel> ue,
                                        Ptr<MobilityModel> satellite,
                                        double feederMinElevationDeg,
                                        double serviceMinElevationDeg)
{
    NS_ABORT_MSG_IF(!gnb || !ue || !satellite, "Visibility needs gNB, UE and satellite");
    NS_ABORT_MSG_IF(!std::isfinite(feederMinElevationDeg) ||
                        !std::isfinite(serviceMinElevationDeg) ||
                        feederMinElevationDeg < 0 || feederMinElevationDeg > 90 ||
                        serviceMinElevationDeg < 0 || serviceMinElevationDeg > 90,
                    "Elevation masks must be finite and in [0, 90] degrees");
    m_gnb = gnb;
    m_ue = ue;
    m_satellite = satellite;
    m_feederMinElevationDeg = feederMinElevationDeg;
    m_serviceMinElevationDeg = serviceMinElevationDeg;
}

/** Project the ground-to-relay ray onto the observer radial vertical and convert its arcsine into
 * elevation degrees.
 */
double
BentPipePropagationLossModel::ElevationDeg(Ptr<const MobilityModel> ground,
                                           Ptr<const MobilityModel> satellite)
{
    const Vector origin = ground->GetPosition();
    const Vector ray = satellite->GetPosition() - origin;
    const double denominator = origin.GetLength() * ray.GetLength();
    NS_ABORT_MSG_IF(!(denominator > 0), "Elevation needs nonzero ECEF ground/range");
    const double sine = (ray.x * origin.x + ray.y * origin.y + ray.z * origin.z) /
                        denominator;
    return std::asin(std::clamp(sine, -1.0, 1.0)) * 180.0 / M_PI;
}

/** Evaluate gateway-to-selected-relay elevation from the current ECEF positions.
 */
double
BentPipePropagationLossModel::GetFeederElevationDeg() const
{
    return ElevationDeg(m_gnb, m_satellite);
}

/** Evaluate UE-to-selected-relay elevation from the current ECEF positions.
 */
double
BentPipePropagationLossModel::GetServiceElevationDeg() const
{
    return ElevationDeg(m_ue, m_satellite);
}

/** Require relay availability and both current hop elevations to meet their configured masks.
 */
bool
BentPipePropagationLossModel::IsVisible() const
{
    return m_available && GetFeederElevationDeg() >= m_feederMinElevationDeg &&
           GetServiceElevationDeg() >= m_serviceMinElevationDeg;
}

/** Preserve input power on a usable path; otherwise return an effectively blocked level before
 * spectrum-budget calculations.
 */
double
BentPipePropagationLossModel::DoCalcRxPower(double txPowerDbm,
                                           Ptr<MobilityModel> a,
                                           Ptr<MobilityModel> b) const
{
    // Above the channel's default MaxLossDb (1e9): no StartRx, no NTN lookup.
    return Resolve(a,b)->IsVisible() ? txPowerDbm : -1e12;
}

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
BentPipeVisibilityFilter::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipeVisibilityFilter")
                            .SetParent<SpectrumTransmitFilter>()
                            .SetGroupName("Spectrum")
                            .AddConstructor<BentPipeVisibilityFilter>();
    return tid;
}

/** Store the shared visibility gate consulted for every transmitted burst.
 */
void
BentPipeVisibilityFilter::Configure(Ptr<BentPipePropagationLossModel> visibility)
{
    m_visibility = visibility;
}

/** Suppress and count transmissions while either hop is hidden or the relay is interrupted; pass
 * usable paths unchanged.
 */
bool
BentPipeVisibilityFilter::DoFilter(Ptr<const SpectrumSignalParameters> params,
                                   Ptr<const SpectrumPhy> receiverPhy)
{
    NS_ABORT_MSG_IF(!m_visibility, "Visibility filter is not configured");
    if (!m_visibility->Resolve(params->txPhy->GetMobility(), receiverPhy->GetMobility())->IsVisible())
    {
        ++m_blocked;
        return true;
    }
    return false;
}

/** Return the cumulative number of bursts suppressed by this direction visibility filter.
 */
uint64_t
BentPipeVisibilityFilter::GetBlockedTransmissions() const
{
    return m_blocked;
}

/** Report zero random-stream allocations; this adapter owns no random variables.
 */
int64_t
BentPipeVisibilityFilter::DoAssignStreams(int64_t /*stream*/)
{
    return 0;
}

/** Report zero random-stream allocations; this adapter owns no random variables.
 */
int64_t
BentPipePropagationLossModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
BentPipeDelayModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipeDelayModel")
                            .SetParent<PropagationDelayModel>()
                            .SetGroupName("Propagation")
                            .AddConstructor<BentPipeDelayModel>();
    return tid;
}

/** Store both ground endpoints and the selected-relay proxy so every delay query follows the live
 * two-hop geometry.
 */
void
BentPipeDelayModel::Configure(Ptr<MobilityModel> gnb,
                              Ptr<MobilityModel> ue,
                              Ptr<MobilityModel> satellite)
{
    NS_ABORT_MSG_IF(!gnb || !ue || !satellite,
                    "BentPipeDelayModel needs gNB, UE, and satellite mobility");
    m_gnb = gnb;
    m_ue = ue;
    m_satellite = satellite;
}

/** Divide current gateway-to-relay slant range by light speed to obtain feeder one-way propagation
 * time.
 */
Time
BentPipeDelayModel::GetFeederDelay() const
{
    NS_ABORT_MSG_IF(!m_gnb || !m_satellite, "BentPipeDelayModel is not configured");
    return Seconds(m_gnb->GetDistanceFrom(m_satellite) / kSpeedOfLightMps);
}

/** Divide current UE-to-relay slant range by light speed to obtain service one-way propagation
 * time.
 */
Time
BentPipeDelayModel::GetServiceDelay() const
{
    NS_ABORT_MSG_IF(!m_ue || !m_satellite, "BentPipeDelayModel is not configured");
    return Seconds(m_ue->GetDistanceFrom(m_satellite) / kSpeedOfLightMps);
}

/** Sum the live feeder and service one-way propagation times for the bent-pipe channel.
 */
Time
BentPipeDelayModel::GetCurrentDelay() const
{
    return GetFeederDelay() + GetServiceDelay();
}

/** Return twice the feeder one-way delay as the common timing-advance component.
 */
Time
BentPipeDelayModel::GetCommonTimingAdvance() const
{
    return GetFeederDelay() * 2;
}

/** Return twice the service one-way delay as the UE-specific timing-advance component.
 */
Time
BentPipeDelayModel::GetUeSpecificTimingAdvance() const
{
    return GetServiceDelay() * 2;
}

/** Return twice the sum of both one-way delays as the total timing advance.
 */
Time
BentPipeDelayModel::GetTotalTimingAdvance() const
{
    return GetCurrentDelay() * 2;
}

/** Implement the channel delay interface using the configured live bent-pipe geometry rather than
 * the direct ground-to-ground distance.
 */
Time
BentPipeDelayModel::GetDelay(Ptr<MobilityModel> a, Ptr<MobilityModel> b) const
{
    for (auto endpoint : {a,b}) {
        auto it=m_paths.find(endpoint);
        if(it!=m_paths.end()) return it->second->GetCurrentDelay();
    }
    return GetCurrentDelay();
}

/** Report zero random-stream allocations; this adapter owns no random variables.
 */
int64_t
BentPipeDelayModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
BentPipeSpectrumModel::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BentPipeSpectrumModel")
                            .SetParent<PhasedArraySpectrumPropagationLossModel>()
                            .SetGroupName("Spectrum")
                            .AddConstructor<BentPipeSpectrumModel>();
    return tid;
}

/** Store independent hop budgets and excess losses, converting receiver noise figures into noise
 * PSDs referenced to minus 174 dBm/Hz.
 */
void
BentPipeSpectrumModel::Configure(Ptr<BentPipePropagationLossModel> visibility,
                                 Ptr<MobilityModel> satellite,
                                 Ptr<ThreeGppPropagationLossModel> firstHopLoss,
                                 Ptr<ThreeGppPropagationLossModel> secondHopLoss,
                                 double sourceGainDb,
                                 double satelliteRxGainDb,
                                 double satelliteEirpDensityDbwMhz,
                                 double destinationGainDb,
                                 double satelliteNoiseFigureDb,
                                 double destinationNoiseFigureDb,
                                 double firstExcessLossDb,
                                 double secondExcessLossDb)
{
    NS_ABORT_MSG_IF(!visibility || !satellite || !firstHopLoss || !secondHopLoss,
                    "Two-hop spectrum model needs geometry and both hop models");
    m_visibility = visibility;
    m_satellite = satellite;
    m_firstHopLoss = firstHopLoss;
    m_secondHopLoss = secondHopLoss;
    m_sourceGainDb = sourceGainDb;
    m_satelliteRxGainDb = satelliteRxGainDb;
    m_satelliteEirpDensityDbwMhz = satelliteEirpDensityDbwMhz;
    m_destinationGainDb = destinationGainDb;
    m_firstExcessLossDb = firstExcessLossDb;
    m_secondExcessLossDb = secondExcessLossDb;
    // Match NrSpectrumValueHelper's -174 dBm/Hz reference noise exactly.
    m_satelliteNoisePsd = std::pow(10.0, (-204.0 + satelliteNoiseFigureDb) / 10.0);
    m_destinationNoisePsd = std::pow(10.0, (-204.0 + destinationNoiseFigureDb) / 10.0);
}

/** Combine first/second-hop SNR by the reciprocal-sum rule and map it to destination-noise-
 * referenced receive PSD, preserving silent bins.
 */
double
BentPipeSpectrumModel::EquivalentRxPsd(double sourcePsd,
                                      double firstHopLossDb,
                                      double secondHopLossDb) const
{
    if (!(sourcePsd > 0.0))
    {
        return 0.0;
    }
    const double firstRx = sourcePsd * std::pow(
        10.0, (m_sourceGainDb + m_satelliteRxGainDb - firstHopLossDb - m_firstExcessLossDb) / 10.0);
    const double secondRx = std::pow(
        10.0, (m_satelliteEirpDensityDbwMhz - 60.0 + m_destinationGainDb -
               secondHopLossDb - m_secondExcessLossDb) / 10.0);
    // SNS3's reciprocal composite-SINR rule, in the noise-limited case.
    // Map to an equivalent signal PSD; NR still adds destination noise once.
    // Stable series-combination form avoids enormous inverse SNR values.
    const double firstEquivalent = firstRx * m_destinationNoisePsd / m_satelliteNoisePsd;
    if (!(firstEquivalent > 0.0) || !(secondRx > 0.0))
    {
        return 0.0;
    }
    return std::min(firstEquivalent, secondRx) /
           (1.0 + std::min(firstEquivalent, secondRx) / std::max(firstEquivalent, secondRx));
}

/** Copy a burst, gate hidden paths, calculate both live NTN hop losses, and transform each occupied
 * spectrum bin into its equivalent two-hop receive PSD.
 */
Ptr<SpectrumSignalParameters>
BentPipeSpectrumModel::DoCalcRxPowerSpectralDensity(
    Ptr<const SpectrumSignalParameters> params,
    Ptr<const MobilityModel> a,
    Ptr<const MobilityModel> b,
    Ptr<const PhasedArrayModel> /*aPhasedArrayModel*/,
    Ptr<const PhasedArrayModel> /*bPhasedArrayModel*/) const
{
    auto result = params->Copy();
    result->psd = Copy<SpectrumValue>(params->psd);
    const auto path=m_visibility->Resolve(a,b);
    const auto relay=path->GetSatellite();
    if (!path->IsVisible())
    {
        *result->psd = 0.0;
        return result;
    }
    const double firstLoss = -m_firstHopLoss->CalcRxPower(0.0, ConstCast<MobilityModel>(a),
                                                        relay);
    const double secondLoss = -m_secondHopLoss->CalcRxPower(0.0, relay,
                                                          ConstCast<MobilityModel>(b));
    for (auto value = result->psd->ValuesBegin(); value != result->psd->ValuesEnd(); ++value)
    {
        *value = EquivalentRxPsd(*value, firstLoss, secondLoss);
    }
    return result;
}

/** Report zero random-stream allocations; this adapter owns no random variables.
 */
int64_t
BentPipeSpectrumModel::DoAssignStreams(int64_t stream)
{
    return 0;
}

} // namespace ns3
