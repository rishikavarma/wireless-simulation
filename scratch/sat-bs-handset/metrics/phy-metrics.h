/*
 * NR PHY SINR samples. DL data comes from NrUePhy DlDataSinr; UL from
 * NrGnbPhy UlSinrTrace.
 */

#ifndef SAT_BS_HANDSET_COMMON_PHY_METRICS_H
#define SAT_BS_HANDSET_COMMON_PHY_METRICS_H

#include "ns3/net-device.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"
#include "ns3/simple-ref-count.h"
#include "ns3/spectrum-value.h"

#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

namespace ns3
{

struct SinrSeriesStats
{
    uint64_t samples{0};
    double minDb{0.0};
    double maxDb{0.0};
    double meanDb{0.0};
};

struct NrPhyMetrics
{
    SinrSeriesStats dlData;
    SinrSeriesStats ul;
};

/** Latest PHY report copied onto a Sage tick. Empty until the first report. */
struct HeldSinr
{
    bool haveDl{false};
    double dlDb{0.0};
    bool haveUl{false};
    double ulDb{0.0};
};

/** Owns NR PHY SINR samples. */
class NrPhyMetricsCollector : public SimpleRefCount<NrPhyMetricsCollector>
{
  public:
    void Connect(Ptr<NetDevice> ueNrDevice, Ptr<NetDevice> gnbDevice);
    void OpenSinrTraces(const std::string& dlPath, const std::string& ulPath);
    /** Trace files keep reports inside [start, end]. Held values still update. */
    void SetRecordWindow(Time start, Time end);
    void CloseTraces();
    /** Latest downlink and uplink SINR whose reports have already fired. */
    HeldSinr LatestSinr() const;
    NrPhyMetrics GetMetrics() const;

  private:
    void DlDataSinr(uint16_t cellId, uint16_t rnti, double avgSinrLinear, uint16_t bwpId);
    void UlSinr(uint64_t imsi, SpectrumValue& sinr, SpectrumValue& power);

    bool Recording(Time now) const;
    static bool AddSample(std::vector<double>& dbSamples, double sinrLinear);
    static SinrSeriesStats Summarize(const std::vector<double>& dbSamples);

    std::vector<double> m_dlDataDb;
    std::vector<double> m_ulDb;
    bool m_haveDl{false};
    double m_dlHeldDb{0.0};
    bool m_haveUl{false};
    double m_ulHeldDb{0.0};
    bool m_limitRecord{false};
    Time m_recordStart;
    Time m_recordEnd;
    std::ofstream m_dlTrace;
    std::ofstream m_ulTrace;
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_PHY_METRICS_H */
