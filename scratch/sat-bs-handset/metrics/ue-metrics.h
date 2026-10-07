/*
 * UE metrics: application message counts plus the TCP socket state Sage uses.
 *
 * The 69 TCP features are four instantaneous values, then six signals each
 * summarized as average / minimum / maximum over the last 10, 200, and 1,000
 * observations, then eleven ratios. Scales match the released Sage collector:
 *   srtt, windowed RTT          microseconds / 1e5   (1.0 == 100 ms)
 *   rttvar, windowed rtt_var    microseconds / 1e3   (1.0 == 1 ms)
 *   thr, dr, dr_max, loss_db    fraction of 100 Mbps
 *   inflight                    unacked packets / 1000
 *   lost                        cumulative lost packets / 100
 * ca_state is the Linux/ns-3 enum: Open=0, Disorder=1, Cwr=2, Recovery=3, Loss=4.
 * pre_act is log2 of the congestion-window change since the previous observation.
 * This simulation has no external Sage action, so that change is TCP's own.
 * A sample is recorded every 5 ms from connection to application stop.
 * Each row also carries the latest downlink and uplink SINR already reported
 * at that tick. A direction stays empty until its first report.
 */

#ifndef SAT_BS_HANDSET_COMMON_UE_METRICS_H
#define SAT_BS_HANDSET_COMMON_UE_METRICS_H

#include "common/app-protocol.h"
#include "metrics/phy-metrics.h"

#include "ns3/event-id.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"
#include "ns3/sequence-number.h"
#include "ns3/tcp-socket-state.h"

#include <cstdint>
#include <deque>
#include <fstream>
#include <string>

namespace ns3
{

class Socket;

constexpr uint32_t kSageShort = 10;
constexpr uint32_t kSageMid = 200;
constexpr uint32_t kSageLong = 1000;

struct SageWindow
{
    double sAvg{0.0};
    double sMin{0.0};
    double sMax{0.0};
    double mAvg{0.0};
    double mMin{0.0};
    double mMax{0.0};
    double lAvg{0.0};
    double lMin{0.0};
    double lMax{0.0};
};

struct SageFeatures
{
    uint64_t observations{0};

    double srtt{0.0};
    double rttvar{0.0};
    double thr{0.0};
    double caState{0.0};

    SageWindow rtt;
    SageWindow thrWin;
    SageWindow rttRate;
    SageWindow rttVar;
    SageWindow inflight;
    SageWindow lost;

    double timeDelta{0.0};
    double rttRateNow{0.0};
    double lossDb{0.0};
    double ackedRate{0.0};
    double drRatio{0.0};
    double bdpCwnd{0.0};
    double dr{0.0};
    double cwndUnackedRate{0.0};
    double drMax{0.0};
    double drMaxRatio{0.0};
    double preAct{0.0};
};

struct UeAppMetrics
{
    AppWorkload workload{AppWorkload::Probe};
    uint64_t sent{0};
    uint64_t received{0};
    uint64_t bytesSent{0};
    uint64_t bytesReceived{0};
    uint64_t lost{0};
    double lossRatio{0.0};
    double deliveryRatio{0.0};
    uint64_t fileChunks{0};
    uint64_t fileAcked{0};
    uint64_t rpcCompleted{0};
    SageFeatures tcp;
    bool success{false};
};

class UeMetrics
{
  public:
    void Start(Ptr<Socket> socket);
    void Stop();
    void OpenTcpTrace(const std::string& path);
    void SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy);
    /** Keep collector updates, and write rows only inside [start, end]. */
    void SetRecordWindow(Time start, Time end);

    void NoteSend(uint32_t bytes);
    void NoteRecv(uint32_t bytes);

    uint64_t Sent() const;
    UeAppMetrics GetMetrics() const;

  private:
    class History
    {
      public:
        explicit History(std::size_t cap);
        void Add(double value);
        double Sum(std::size_t n) const;
        double Avg(std::size_t n) const;
        double Min(std::size_t n) const;
        double Max(std::size_t n) const;

      private:
        std::deque<double> m_q;
        std::size_t m_cap;
    };

    void Poll();
    void Commit();
    void WriteTcpRow(Time now);
    bool Recording(Time now) const;
    static SageWindow Summarize(const History& history);

    void OnSrtt(Time oldRtt, Time newRtt);
    void OnLastRtt(Time oldRtt, Time newRtt);
    void OnCwnd(uint32_t oldCwnd, uint32_t newCwnd);
    void OnCaState(TcpSocketState::TcpCongState_t oldState,
                   TcpSocketState::TcpCongState_t newState);
    void OnHighestSequence(SequenceNumber32 oldSeq, SequenceNumber32 newSeq);

    uint64_t m_sent{0};
    uint64_t m_received{0};
    uint64_t m_bytesSent{0};
    uint64_t m_bytesReceived{0};

    Ptr<Socket> m_socket;
    EventId m_event;
    bool m_running{false};
    uint32_t m_mss{1};
    Time m_srtt;
    Time m_lastRecord;
    double m_rttvarUs{0.0};
    double m_minRttUs{0.0};
    bool m_haveVar{false};
    uint32_t m_cwndBytes{0};
    uint32_t m_prevCwndBytes{0};
    uint8_t m_caState{0};
    bool m_haveTx{false};
    uint32_t m_highTx{0};
    bool m_originSet{false};
    uint32_t m_prevAck{0};
    uint32_t m_prevSnd{0};
    bool m_haveLost{false};
    uint32_t m_lastLostOutstanding{0};
    uint64_t m_pendingLostBytes{0};
    uint64_t m_cumLostBytes{0};
    double m_prevDrMbps{0.0};
    double m_prevDrMaxMbps{0.0};

    History m_rttHist{kSageLong};
    History m_thrHist{kSageLong};
    History m_rttRateHist{kSageLong};
    History m_rttVarHist{kSageLong};
    History m_inflightHist{kSageLong};
    History m_lostHist{kSageLong};
    History m_sentBytes{100};
    History m_dlvBytes{100};
    History m_lossBytes{100};
    History m_unackedBytes{100};
    History m_dtUs{100};
    History m_drMbps{kSageMid};
    History m_sendBps{5000};

    SageFeatures m_tcp;
    double m_lostNow{0.0};
    Ptr<NrPhyMetricsCollector> m_phy;
    bool m_limitRecord{false};
    Time m_recordStart;
    Time m_recordEnd;
    std::ofstream m_tcpTrace;
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_UE_METRICS_H */
