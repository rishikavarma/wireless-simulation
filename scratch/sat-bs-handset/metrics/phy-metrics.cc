#include "metrics/phy-metrics.h"

#include "metrics/series-stats.h"
#include "topology/bent-pipe-channel.h"

#include "ns3/abort.h"
#include "ns3/nr-gnb-phy.h"
#include "ns3/nr-module.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/simulator.h"

#include <cmath>
#include <iomanip>

namespace ns3
{

namespace
{

/** Average the linear SINR across all reported spectrum bins, returning zero for an empty spectrum.
 */
double
AvgSpectrumLinear(SpectrumValue& v)
{
    double sum = 0.0;
    uint32_t n = 0;
    for (auto it = v.ValuesBegin(); it != v.ValuesEnd(); ++it)
    {
        sum += (*it);
        ++n;
    }
    return (n > 0) ? (sum / static_cast<double>(n)) : 0.0;
}

} // namespace

/** Destroy the collector; stream members flush and close through their own destructors. */
NrPhyMetricsCollector::~NrPhyMetricsCollector() = default;

/** Accept only finite positive linear SINR values, convert them to decibels, and append them to the
 * supplied series.
 */
bool
NrPhyMetricsCollector::AddSample(std::vector<double>& dbSamples, double sinrLinear)
{
    if (!(sinrLinear > 0.0) || !std::isfinite(sinrLinear))
    {
        return false;
    }
    dbSamples.push_back(10.0 * std::log10(sinrLinear));
    return true;
}

/** Convert the generic count/minimum/maximum/mean summary into the PHY metric field layout.
 */
SinrSeriesStats
NrPhyMetricsCollector::Summarize(const std::vector<double>& dbSamples)
{
    const SeriesStats g = SummarizeSeries(dbSamples);
    SinrSeriesStats s;
    s.samples = g.samples;
    s.minDb = g.min;
    s.maxDb = g.max;
    s.meanDb = g.mean;
    return s;
}

/** Open separate downlink/uplink CSV streams, fail on open errors, and write the timestamp/SINR
 * headers.
 */
void
NrPhyMetricsCollector::OpenSinrTraces(const std::string& dlPath, const std::string& ulPath)
{
    m_dlTrace.open(dlPath);
    m_ulTrace.open(ulPath);
    NS_ABORT_MSG_IF(!m_dlTrace, "cannot open downlink SINR trace " << dlPath);
    NS_ABORT_MSG_IF(!m_ulTrace, "cannot open uplink SINR trace " << ulPath);
    const char* header = "# time_s is simulation time. sinr_db is one PHY report.\n"
                         "time_s,sinr_db\n";
    m_dlTrace << std::setprecision(8) << header;
    m_ulTrace << std::setprecision(8) << header;
}

/** Enable an inclusive time filter for PHY CSV writes while continuing to collect run-wide
 * summaries.
 */
void
NrPhyMetricsCollector::SetRecordWindow(Time start, Time end)
{
    m_limitRecord = true;
    m_recordStart = start;
    m_recordEnd = end;
}

/** Close each open PHY CSV stream so its buffered samples are written to disk.
 */
void
NrPhyMetricsCollector::CloseTraces()
{
    if (m_dlTrace.is_open())
    {
        m_dlTrace.close();
    }
    if (m_ulTrace.is_open())
    {
        m_ulTrace.close();
    }
}

/** Check whether a timestamp lies inside the configured inclusive recording interval, or accept it
 * when no interval was set.
 */
bool
NrPhyMetricsCollector::Recording(Time now) const
{
    return !m_limitRecord || (now >= m_recordStart && now <= m_recordEnd);
}

/** Attach the UE BWP-0 downlink-data SINR and gNB BWP-1 allocated-RB uplink TB callbacks, aborting if
 * either PHY is absent.
 */
void
NrPhyMetricsCollector::Connect(Ptr<NetDevice> ueNrDevice, Ptr<NetDevice> gnbDevice)
{
    constexpr uint16_t dlBwpId = 0;
    constexpr uint16_t ulBwpId = 1;
    Ptr<NrUePhy> uePhy = NrHelper::GetUePhy(ueNrDevice, dlBwpId);
    NS_ABORT_MSG_IF(!uePhy, "UE NR PHY missing for SINR traces");
    uePhy->TraceConnectWithoutContext("DlDataSinr",
                                      MakeCallback(&NrPhyMetricsCollector::DlDataSinr, this));

    Ptr<NrGnbPhy> gnbPhy = NrHelper::GetGnbPhy(gnbDevice, ulBwpId);
    NS_ABORT_MSG_IF(!gnbPhy, "gNB NR PHY missing for SINR traces");
    gnbPhy->GetSpectrumPhy()->TraceConnectWithoutContext("RxPacketTraceGnb",
        MakeCallback(&NrPhyMetricsCollector::UlTb,this).Bind(ueNrDevice));
}

/** Store a valid downlink report, update its held value, and append the timestamp/value to CSV when
 * inside the recording interval.
 */
void
NrPhyMetricsCollector::DlDataSinr(uint16_t /*cellId*/,
                                  uint16_t /*rnti*/,
                                  double avgSinrLinear,
                                  uint16_t /*bwpId*/)
{
    if (!AddSample(m_dlDataDb, avgSinrLinear))
    {
        return;
    }
    m_haveDl = true;
    m_dlHeldDb = m_dlDataDb.back();
    if (m_dlTrace.is_open() && Recording(Simulator::Now()))
    {
        m_dlTrace << Simulator::Now().GetSeconds() << ',' << m_dlHeldDb << '\n';
    }
}

void NrPhyMetricsCollector::UlTb(Ptr<NetDevice> ue, RxPacketTraceParams p)
{
    if(p.m_rnti!=DynamicCast<NrUeNetDevice>(ue)->GetRrc()->GetRnti()) return;
    if(!AddSample(m_ulDb,p.m_sinr)) return;
    m_haveUl=true;m_ulHeldDb=m_ulDb.back();
    if(m_ulTrace.is_open() && Recording(Simulator::Now()))
        m_ulTrace<<Simulator::Now().GetSeconds()<<','<<m_ulHeldDb<<'\n';
}

/** Average an uplink SINR spectrum in linear units, store it in decibels, and update held/CSV
 * values under the recording filter.
 */
void
NrPhyMetricsCollector::UlSinr(uint64_t /*imsi*/, SpectrumValue& sinr, SpectrumValue& /*power*/)
{
    if (!AddSample(m_ulDb, AvgSpectrumLinear(sinr)))
    {
        return;
    }
    m_haveUl = true;
    m_ulHeldDb = m_ulDb.back();
    if (m_ulTrace.is_open() && Recording(Simulator::Now()))
    {
        m_ulTrace << Simulator::Now().GetSeconds() << ',' << m_ulHeldDb << '\n';
    }
}

/** Return held per-direction SINR and availability flags; suppress both while the active relay path
 * is unavailable or hidden.
 */
HeldSinr
NrPhyMetricsCollector::LatestSinr() const
{
    HeldSinr held;
    if (m_visibility && !m_visibility->IsVisible())
    {
        return held;
    }
    held.haveDl = m_haveDl;
    held.dlDb = m_dlHeldDb;
    held.haveUl = m_haveUl;
    held.ulDb = m_ulHeldDb;
    return held;
}

/** Attach the shared relay visibility gate used to suppress stale held SINR during outages.
 */
void
NrPhyMetricsCollector::SetVisibility(Ptr<BentPipePropagationLossModel> visibility)
{
    m_visibility = visibility;
}

/** Return separate all-run statistical summaries of recorded downlink and uplink SINR samples.
 */
NrPhyMetrics
NrPhyMetricsCollector::GetMetrics() const
{
    NrPhyMetrics m;
    m.dlData = Summarize(m_dlDataDb);
    m.ul = Summarize(m_ulDb);
    return m;
}

} // namespace ns3
