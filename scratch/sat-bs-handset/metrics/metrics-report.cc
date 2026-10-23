#include "metrics/metrics-report.h"

#include "common/app-protocol.h"

namespace ns3
{

namespace
{

const char*
/** Translate the numeric TCP congestion state into its readable log label, using Unknown for
 * unsupported states.
 */
CaStateName(double state)
{
    switch (static_cast<int>(state))
    {
    case 0:
        return "Open";
    case 1:
        return "Disorder";
    case 2:
        return "Cwr";
    case 3:
        return "Recovery";
    case 4:
        return "Loss";
    default:
        return "Unknown";
    }
}

/** Write the short, middle, and long sample-window average/minimum/maximum values under one metric
 * name.
 */
void
WriteWindow(std::ostream& os, const char* name, const SageWindow& w)
{
    os << "metrics.sage." << name << " s.avg=" << w.sAvg << " s.min=" << w.sMin
       << " s.max=" << w.sMax << " m.avg=" << w.mAvg << " m.min=" << w.mMin << " m.max=" << w.mMax
       << " l.avg=" << w.lAvg << " l.min=" << w.lMin << " l.max=" << w.lMax << '\n';
}

/** Write a PHY series sample count and mean/minimum/maximum in decibels.
 */
void
WriteSinr(std::ostream& os, const char* name, const SinrSeriesStats& s)
{
    os << "metrics.sinr." << name << "_dB samples=" << s.samples << " avg=" << s.meanDb
       << " min=" << s.minDb << " max=" << s.maxDb << '\n';
}

} // namespace

/** Serialize UE application/TCP features, server counters, and PHY summaries to the common
 * console/summary-text format.
 */
void
WriteRunMetrics(std::ostream& os,
                Ipv4Address ueAddr,
                Ipv4Address bsAddr,
                const UeAppMetrics& ue,
                const BsAppMetrics& bs,
                const NrPhyMetrics& phy)
{
    const SageFeatures& sage = ue.tcp;

    os << "path UE " << ueAddr << " --bent-pipe--> BS " << bsAddr << " (app)\n";
    os << "metrics.ue workload=" << (ue.workloadLabel.empty()?AppWorkloadToString(ue.workload):ue.workloadLabel) << " sent=" << ue.sent
       << " recv=" << ue.received << " lost=" << ue.lost << " delivery=" << ue.deliveryRatio
       << " lossRatio=" << ue.lossRatio << " bytesTx=" << ue.bytesSent
       << " bytesRx=" << ue.bytesReceived << '\n';

    os << "metrics.sage observations=" << sage.observations
       << " scale srtt=us/1e5 rttvar=us/1e3 thr=fraction_of_100Mbps"
       << " inflight=pkts/1000 lost=pkts/100\n";
    os << "metrics.sage.now srtt=" << sage.srtt << " rttvar=" << sage.rttvar << " thr=" << sage.thr
       << " ca_state=" << sage.caState << "(" << CaStateName(sage.caState) << ")\n";
    WriteWindow(os, "rtt", sage.rtt);
    WriteWindow(os, "thr", sage.thrWin);
    WriteWindow(os, "rtt_rate", sage.rttRate);
    WriteWindow(os, "rtt_var", sage.rttVar);
    WriteWindow(os, "inflight", sage.inflight);
    WriteWindow(os, "lost", sage.lost);
    os << "metrics.sage.derived time_delta=" << sage.timeDelta << " rtt_rate=" << sage.rttRateNow
       << " loss_db=" << sage.lossDb << " acked_rate=" << sage.ackedRate
       << " dr_ratio=" << sage.drRatio << " bdp_cwnd=" << sage.bdpCwnd << " dr=" << sage.dr
       << " cwnd_unacked_rate=" << sage.cwndUnackedRate << " dr_max=" << sage.drMax
       << " dr_max_ratio=" << sage.drMaxRatio << " pre_act=" << sage.preAct << '\n';

    os << "metrics.bs requests=" << bs.requests << " replies=" << bs.replies
       << " bytesRx=" << bs.bytesReceived << " bytesTx=" << bs.bytesSent << '\n';
    WriteSinr(os, "dlData", phy.dlData);
    WriteSinr(os, "ul", phy.ul);
}

/** Write the workload success flag, delivery/byte counters, and file/RPC-specific completion
 * criterion to the application report.
 */
void
WriteAppOutcome(std::ostream& os, const UeAppMetrics& ue)
{
    os << "application workload=" << (ue.workloadLabel.empty()?AppWorkloadToString(ue.workload):ue.workloadLabel)
       << " success=" << (ue.success ? 1 : 0) << '\n';
    os << "application sent=" << ue.sent << " recv=" << ue.received
       << " delivery=" << ue.deliveryRatio << " bytesTx=" << ue.bytesSent
       << " bytesRx=" << ue.bytesReceived << '\n';
    if (!ue.criterion.empty())
    {
        os << "application criterion=" << ue.criterion << '\n';
        os << "application accounting=accepted_vs_delivered_bytes_at_cutoff;shortfall_includes_backlog\n";
        return;
    }
    switch (ue.workload)
    {
    case AppWorkload::File:
        os << "application files=" << ue.filesCompleted << " chunksPerFile=" << ue.fileChunks
           << " ackedThisFile=" << ue.fileAcked << '\n';
        os << "application criterion=files>0\n";
        break;
    case AppWorkload::Rpc:
        os << "application completed=" << ue.rpcCompleted << '\n';
        os << "application criterion=completed>0 and completed>=0.9*sent\n";
        break;
    case AppWorkload::Probe:
    case AppWorkload::Stream:
    case AppWorkload::Iot:
        os << "application criterion=sent>0 and recv>=0.9*sent\n";
        break;
    }
}

} // namespace ns3
