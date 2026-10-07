#include "metrics/phy-metrics.h"

#include "metrics/series-stats.h"

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

void
NrPhyMetricsCollector::SetRecordWindow(Time start, Time end)
{
    m_limitRecord = true;
    m_recordStart = start;
    m_recordEnd = end;
}

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

bool
NrPhyMetricsCollector::Recording(Time now) const
{
    return !m_limitRecord || (now >= m_recordStart && now <= m_recordEnd);
}

void
NrPhyMetricsCollector::Connect(Ptr<NetDevice> ueNrDevice, Ptr<NetDevice> gnbDevice)
{
    Ptr<NrUePhy> uePhy = NrHelper::GetUePhy(ueNrDevice, 0);
    NS_ABORT_MSG_IF(!uePhy, "UE NR PHY missing for SINR traces");
    uePhy->TraceConnectWithoutContext("DlDataSinr",
                                      MakeCallback(&NrPhyMetricsCollector::DlDataSinr, this));

    Ptr<NrGnbPhy> gnbPhy = NrHelper::GetGnbPhy(gnbDevice, 0);
    NS_ABORT_MSG_IF(!gnbPhy, "gNB NR PHY missing for SINR traces");
    gnbPhy->TraceConnectWithoutContext("UlSinrTrace",
                                       MakeCallback(&NrPhyMetricsCollector::UlSinr, this));
}

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

HeldSinr
NrPhyMetricsCollector::LatestSinr() const
{
    HeldSinr held;
    held.haveDl = m_haveDl;
    held.dlDb = m_dlHeldDb;
    held.haveUl = m_haveUl;
    held.ulDb = m_ulHeldDb;
    return held;
}

NrPhyMetrics
NrPhyMetricsCollector::GetMetrics() const
{
    NrPhyMetrics m;
    m.dlData = Summarize(m_dlDataDb);
    m.ul = Summarize(m_ulDb);
    return m;
}

} // namespace ns3
