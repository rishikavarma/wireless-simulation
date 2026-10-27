#include "metrics/ue-metrics.h"

#include "ns3/abort.h"
#include "ns3/simulator.h"
#include "ns3/tcp-socket-base.h"
#include "ns3/tcp-tx-buffer.h"
#include "ns3/uinteger.h"

#include <algorithm>
#include <cmath>
#include <iomanip>

namespace ns3
{

namespace
{

constexpr double kRttUnitUs = 100000.0;
constexpr double kRttVarUnitUs = 1000.0;
constexpr double kBwNormMbps = 100.0;
constexpr double kBytesPerSecPerMbps = 125000.0;
constexpr double kInflightUnitPkts = 1000.0;
constexpr double kLostUnitPkts = 100.0;
const Time kSamplePeriod = MilliSeconds(5);

/** Compute a wrapping uint32 sequence advance and reject differences larger than half the sequence
 * space as backward movement.
 */
uint32_t
ForwardDelta(uint32_t now, uint32_t prev)
{
    const uint32_t delta = now - prev;
    return delta > 0x80000000u ? 0u : delta;
}

/** Append the nine short/middle/long average/minimum/maximum CSV column names for one metric
 * signal.
 */
void
AppendWindowHeader(std::ostream& os, const char* name)
{
    os << ',' << name << "_s_avg," << name << "_s_min," << name << "_s_max," << name
       << "_m_avg," << name << "_m_min," << name << "_m_max," << name << "_l_avg," << name
       << "_l_min," << name << "_l_max";
}

/** Append the nine stored window summary values in the same order as their CSV headers.
 */
void
AppendWindow(std::ostream& os, const SageWindow& window)
{
    os << ',' << window.sAvg << ',' << window.sMin << ',' << window.sMax << ',' << window.mAvg
       << ',' << window.mMin << ',' << window.mMax << ',' << window.lAvg << ',' << window.lMin
       << ',' << window.lMax;
}

} // namespace

/** Set the maximum retained sample count for this rolling history buffer.
 */
UeMetrics::History::History(std::size_t cap)
    : m_cap(cap)
{
}

/** Append one observation and evict the oldest value if the configured sample capacity is exceeded.
 */
void
UeMetrics::History::Add(double value)
{
    m_q.push_back(value);
    if (m_q.size() > m_cap)
    {
        m_q.pop_front();
    }
}

/** Sum up to the requested number of newest history samples, returning zero for an empty selection.
 */
double
UeMetrics::History::Sum(std::size_t n) const
{
    if (m_q.empty() || n == 0)
    {
        return 0.0;
    }
    const std::size_t take = std::min(n, m_q.size());
    double sum = 0.0;
    for (auto it = m_q.end() - static_cast<std::ptrdiff_t>(take); it != m_q.end(); ++it)
    {
        sum += *it;
    }
    return sum;
}

/** Divide the newest-sample sum by the available requested count, returning zero when no samples
 * are selected.
 */
double
UeMetrics::History::Avg(std::size_t n) const
{
    if (m_q.empty() || n == 0)
    {
        return 0.0;
    }
    const std::size_t take = std::min(n, m_q.size());
    return Sum(n) / static_cast<double>(take);
}

/** Return the minimum among the requested newest samples, or zero for an empty selection.
 */
double
UeMetrics::History::Min(std::size_t n) const
{
    if (m_q.empty() || n == 0)
    {
        return 0.0;
    }
    const std::size_t take = std::min(n, m_q.size());
    return *std::min_element(m_q.end() - static_cast<std::ptrdiff_t>(take), m_q.end());
}

/** Return the maximum among the requested newest samples, or zero for an empty selection.
 */
double
UeMetrics::History::Max(std::size_t n) const
{
    if (m_q.empty() || n == 0)
    {
        return 0.0;
    }
    const std::size_t take = std::min(n, m_q.size());
    return *std::max_element(m_q.end() - static_cast<std::ptrdiff_t>(take), m_q.end());
}

/** Construct the nine summary fields from the last 10, 200, and 1000 samples of one rolling
 * history.
 */
SageWindow
UeMetrics::Summarize(const History& history)
{
    return {
           history.Avg(kSageShort),
           history.Min(kSageShort),
           history.Max(kSageShort),
           history.Avg(kSageMid),
           history.Min(kSageMid),
           history.Max(kSageMid),
           history.Avg(kSageLong),
           history.Min(kSageLong),
           history.Max(kSageLong)};
}

/** Reset per-socket TCP state, retain run-level counters/history, attach TCP trace callbacks, and
 * begin 5 ms metric polling.
 */
void
UeMetrics::Start(Ptr<Socket> socket)
{
    m_socket = socket;
    // Per-socket TCP state cannot be carried across a new connection. Keep
    // application counters, cumulative loss and the run's trace file.
    m_srtt = Time();
    m_haveAckProgress = false;
    m_haveRttUpdate = false;
    m_progressPending = false;
    m_pendingBytes = 0;
    m_progressOrigin = Simulator::Now();
    m_rttvarUs = 0.0;
    m_minRttUs = 0.0;
    m_lastRttSampleUs = 0.0;
    m_rttUpdatesSincePoll = 0;
    m_haveVar = false;
    m_cwndBytes = 0;
    m_prevCwndBytes = 0;
    m_caState = 0;
    m_haveTx = false;
    m_highTx = 0;
    m_haveLost = false;
    m_lastLostOutstanding = 0;
    m_pendingLostBytes = 0;
    m_prevDrMbps = 0.0;
    m_prevDrMaxMbps = 0.0;
    if (!DynamicCast<TcpSocketBase>(socket))
    {
        m_running = true;
        m_event = Simulator::Schedule(kSamplePeriod, &UeMetrics::Poll, this);
        return;
    }
    UintegerValue segmentSize;
    socket->GetAttribute("SegmentSize", segmentSize);
    m_mss = segmentSize.Get() == 0 ? 1u : segmentSize.Get();

    Ptr<TcpSocketBase> tcp = DynamicCast<TcpSocketBase>(socket);
    NS_ABORT_MSG_IF(!tcp || !tcp->GetTxBuffer(), "Sage features require a TCP socket");
    const uint32_t head = tcp->GetTxBuffer()->HeadSequence().GetValue();
    m_prevAck = head;
    m_prevSnd = head;
    m_originSet = true;
    m_lastRecord = Simulator::Now();

    socket->TraceConnectWithoutContext("RTT", MakeCallback(&UeMetrics::OnSrtt, this));
    socket->TraceConnectWithoutContext("LastRTT", MakeCallback(&UeMetrics::OnLastRtt, this));
    socket->TraceConnectWithoutContext("CongestionWindow",
                                       MakeCallback(&UeMetrics::OnCwnd, this));
    socket->TraceConnectWithoutContext("CongState", MakeCallback(&UeMetrics::OnCaState, this));
    socket->TraceConnectWithoutContext("HighestSequence",
                                       MakeCallback(&UeMetrics::OnHighestSequence, this));

    m_running = true;
    m_event = Simulator::Schedule(kSamplePeriod, &UeMetrics::Poll, this);
}

/** Open the TCP CSV and write the complete feature schema with scaling, sampling, and held-SINR
 * documentation.
 */
void
UeMetrics::OpenTcpTrace(const std::string& path)
{
    m_tcpTrace.open(path);
    NS_ABORT_MSG_IF(!m_tcpTrace, "cannot open TCP trace " << path);
    const std::string suffix = "-tcp.csv";
    NS_ABORT_MSG_IF(path.size() < suffix.size() ||
                        path.compare(path.size() - suffix.size(), suffix.size(), suffix) != 0,
                    "TCP trace path must end in " << suffix);
    const std::string rttPath = path.substr(0, path.size() - suffix.size()) + "-rtt-samples.csv";
    m_rttSamplesTrace.open(rttPath);
    NS_ABORT_MSG_IF(!m_rttSamplesTrace, "cannot open RTT sample trace " << rttPath);
    m_tcpTrace << std::setprecision(8);
    m_rttSamplesTrace << std::setprecision(12) << "time_s,rtt_us\n";
    m_tcpTrace << "# One row per TCP sample. Samples are taken every 5 ms, including intervals "
                  "with no send, acknowledgment, or loss.\n";
    m_tcpTrace << "# sinr_dl_db and sinr_ul_db are the latest PHY report at or before this "
                  "tick. Empty until the first report of that direction.\n";
    m_tcpTrace << "# srtt us/1e5 (1.0 = 100 ms). rttvar and rtt_var us/1e3. "
                  "thr, loss_db, dr, dr_max are a fraction of 100 Mbps. "
                  "inflight is unacked packets/1000. lost is the cumulative lost packets/100 "
                  "at this tick. "
                  "ca_state Open=0 Disorder=1 Cwr=2 Recovery=3 Loss=4. "
                  "s/m/l are the last 10/200/1000 samples.\n";
    m_tcpTrace << "time_s,srtt,rttvar,thr,ca_state,lost";
    AppendWindowHeader(m_tcpTrace, "rtt");
    AppendWindowHeader(m_tcpTrace, "thr");
    AppendWindowHeader(m_tcpTrace, "rtt_rate");
    AppendWindowHeader(m_tcpTrace, "rtt_var");
    AppendWindowHeader(m_tcpTrace, "inflight");
    AppendWindowHeader(m_tcpTrace, "lost");
    m_tcpTrace << ",time_delta,rtt_rate,loss_db,acked_rate,dr_ratio,bdp_cwnd,dr,"
                  "cwnd_unacked_rate,dr_max,dr_max_ratio,pre_act,sinr_dl_db,sinr_ul_db,"
                  "tcp_pending_bytes,tcp_ack_age_s,tcp_no_progress_s,tcp_rtt_update_age_s,"
                  "minrtt,last_rtt_sample,rtt_update_count,cwnd_bytes,sent_bytes,acked_bytes,"
                  "lost_bytes\n";
}

/** Store the shared PHY collector used to append the latest available SINR to each TCP row.
 */
void
UeMetrics::SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy)
{
    m_phy = phy;
}

/** Set the inclusive CSV recording interval without stopping TCP state updates outside that
 * interval.
 */
void
UeMetrics::SetRecordWindow(Time start, Time end)
{
    m_limitRecord = true;
    m_recordStart = start;
    m_recordEnd = end;
}

/** Return whether a sample time passes the configured inclusive recording interval, or true when
 * filtering is disabled.
 */
bool
UeMetrics::Recording(Time now) const
{
    return !m_limitRecord || (now >= m_recordStart && now <= m_recordEnd);
}

/** Write the current scaled TCP snapshot and window summaries, then append held SINR or empty cells
 * for unobserved/outage directions.
 */
void
UeMetrics::WriteTcpRow(Time now, uint32_t sent, uint32_t delivered, uint64_t lostBytes)
{
    if (!m_tcpTrace.is_open() || !Recording(now))
    {
        return;
    }
    const SageFeatures& f = m_tcp;
    m_tcpTrace << now.GetSeconds() << ',' << f.srtt << ',' << f.rttvar << ',' << f.thr << ','
               << f.caState << ',' << m_lostNow;
    AppendWindow(m_tcpTrace, f.rtt);
    AppendWindow(m_tcpTrace, f.thrWin);
    AppendWindow(m_tcpTrace, f.rttRate);
    AppendWindow(m_tcpTrace, f.rttVar);
    AppendWindow(m_tcpTrace, f.inflight);
    AppendWindow(m_tcpTrace, f.lost);
    m_tcpTrace << ',' << f.timeDelta << ',' << f.rttRateNow << ',' << f.lossDb << ','
               << f.ackedRate << ',' << f.drRatio << ',' << f.bdpCwnd << ',' << f.dr << ','
               << f.cwndUnackedRate << ',' << f.drMax << ',' << f.drMaxRatio << ',' << f.preAct;
    const HeldSinr held = m_phy ? m_phy->LatestSinr() : HeldSinr{};
    m_tcpTrace << ',';
    if (held.haveDl)
    {
        m_tcpTrace << held.dlDb;
    }
    m_tcpTrace << ',';
    if (held.haveUl)
    {
        m_tcpTrace << held.ulDb;
    }
    m_tcpTrace << ',' << m_pendingBytes << ',';
    if (m_haveAckProgress) m_tcpTrace << (now-m_lastAckProgress).GetSeconds();
    else m_tcpTrace << "nan";
    m_tcpTrace << ',' << (m_progressPending ? (now-m_progressOrigin).GetSeconds() : 0.0) << ',';
    if (m_haveRttUpdate) m_tcpTrace << (now-m_lastRttUpdate).GetSeconds();
    else m_tcpTrace << "nan";
    m_tcpTrace << ',';
    if (m_minRttUs > 0.0) m_tcpTrace << m_minRttUs / kRttUnitUs;
    else m_tcpTrace << "nan";
    m_tcpTrace << ',';
    if (m_rttUpdatesSincePoll > 0) m_tcpTrace << m_lastRttSampleUs / kRttUnitUs;
    else m_tcpTrace << "nan";
    m_tcpTrace << ',' << m_rttUpdatesSincePoll << ',' << m_cwndBytes << ',' << sent << ','
               << delivered << ',' << lostBytes;
    m_tcpTrace << '\n';
}

/** Cancel polling, commit one final socket snapshot, detach TCP trace callbacks, and flush the CSV
 * while preserving it for reconnects.
 */
void
UeMetrics::Stop()
{
    if (!m_running)
    {
        return;
    }
    Simulator::Cancel(m_event);
    Commit();
    m_running = false;
    if (m_socket)
    {
        m_socket->TraceDisconnectWithoutContext("RTT", MakeCallback(&UeMetrics::OnSrtt, this));
        m_socket->TraceDisconnectWithoutContext("LastRTT", MakeCallback(&UeMetrics::OnLastRtt, this));
        m_socket->TraceDisconnectWithoutContext("CongestionWindow",
                                               MakeCallback(&UeMetrics::OnCwnd, this));
        m_socket->TraceDisconnectWithoutContext("CongState", MakeCallback(&UeMetrics::OnCaState, this));
        m_socket->TraceDisconnectWithoutContext("HighestSequence",
                                               MakeCallback(&UeMetrics::OnHighestSequence, this));
        m_socket = nullptr;
    }
    if (m_tcpTrace.is_open())
    {
        m_tcpTrace.flush();
    }
    if (m_rttSamplesTrace.is_open())
    {
        m_rttSamplesTrace.flush();
    }
}

/** Retain the latest smoothed RTT supplied by the socket RTT trace.
 */
void
UeMetrics::OnSrtt(Time /*oldRtt*/, Time newRtt)
{
    m_srtt = newRtt;
}

/** Track the minimum positive RTT and update RTT variance with the initial half-sample value or a
 * quarter-weighted absolute RTT error.
 */
void
UeMetrics::OnLastRtt(Time /*oldRtt*/, Time newRtt)
{
    const double sampleUs = newRtt.GetMicroSeconds();
    if (!(sampleUs > 0.0))
    {
        return;
    }
    m_lastRttUpdate = Simulator::Now();
    m_haveRttUpdate = true;
    if (!(m_minRttUs > 0.0) || sampleUs < m_minRttUs)
    {
        m_minRttUs = sampleUs;
    }
    m_lastRttSampleUs = sampleUs;
    ++m_rttUpdatesSincePoll;
    if (m_rttSamplesTrace.is_open() && Recording(Simulator::Now()))
    {
        m_rttSamplesTrace << Simulator::Now().GetSeconds() << ',' << sampleUs << '\n';
    }
    const double srttUs = m_srtt.IsZero() ? sampleUs : m_srtt.GetMicroSeconds();
    const double err = std::abs(sampleUs - srttUs);
    if (!m_haveVar)
    {
        m_rttvarUs = sampleUs / 2.0;
        m_haveVar = true;
    }
    else
    {
        m_rttvarUs = (0.75 * m_rttvarUs) + (0.25 * err);
    }
}

/** Retain the latest TCP congestion window in bytes for subsequent metric sampling.
 */
void
UeMetrics::OnCwnd(uint32_t /*oldCwnd*/, uint32_t newCwnd)
{
    m_cwndBytes = newCwnd;
}

/** Retain the socket congestion-state enum as its numeric metric value.
 */
void
UeMetrics::OnCaState(TcpSocketState::TcpCongState_t /*oldState*/,
                         TcpSocketState::TcpCongState_t newState)
{
    m_caState = static_cast<uint8_t>(newState);
}

/** Retain the latest highest transmitted sequence and mark transmit sequence state available.
 */
void
UeMetrics::OnHighestSequence(SequenceNumber32 /*oldSeq*/, SequenceNumber32 newSeq)
{
    m_highTx = newSeq.GetValue();
    m_haveTx = true;
}

/** Commit a snapshot while sampling is active and schedule the next update five milliseconds later.
 */
void
UeMetrics::Poll()
{
    if (!m_running)
    {
        return;
    }
    if (!DynamicCast<TcpSocketBase>(m_socket))
    {
        if (m_tcpTrace.is_open() && Recording(Simulator::Now()))
        {
            m_tcpTrace << Simulator::Now().GetSeconds();
            for (int i = 0; i < 70; ++i) m_tcpTrace << ",nan";
            const HeldSinr held = m_phy ? m_phy->LatestSinr() : HeldSinr{};
            m_tcpTrace << ',';
            if (held.haveDl) m_tcpTrace << held.dlDb; else m_tcpTrace << "nan";
            m_tcpTrace << ',';
            if (held.haveUl) m_tcpTrace << held.ulDb; else m_tcpTrace << "nan";
            m_tcpTrace << ",nan,nan,nan,nan,nan,nan,nan,nan,nan,nan,nan\n";
        }
    }
    else Commit();
    m_event = Simulator::Schedule(kSamplePeriod, &UeMetrics::Poll, this);
}

/** Derive sent/ACKed/lost byte increments from socket state, update scaled Sage rates and rolling
 * histories, and emit a timestamped snapshot.
 */
void
UeMetrics::Commit()
{
    Ptr<TcpSocketBase> tcp = DynamicCast<TcpSocketBase>(m_socket);
    if (!tcp || !tcp->GetTxBuffer() || !m_originSet)
    {
        return;
    }
    Ptr<TcpTxBuffer> tx = tcp->GetTxBuffer();
    const uint32_t ack = tx->HeadSequence().GetValue();
    const uint32_t snd = m_haveTx ? m_highTx : ack;
    const uint32_t lostOutstanding = tx->GetLost();
    if (!m_haveLost)
    {
        m_lastLostOutstanding = lostOutstanding;
        m_haveLost = true;
    }
    else if (lostOutstanding > m_lastLostOutstanding)
    {
        m_pendingLostBytes += lostOutstanding - m_lastLostOutstanding;
        m_lastLostOutstanding = lostOutstanding;
    }
    else
    {
        m_lastLostOutstanding = lostOutstanding;
    }

    const uint32_t delivered = ForwardDelta(ack, m_prevAck);
    const uint32_t sent = ForwardDelta(snd, m_prevSnd);

    const Time now = Simulator::Now();
    if (delivered > 0)
    {
        m_lastAckProgress = now;
        m_haveAckProgress = true;
    }
    m_pendingBytes = tx->Size();
    const bool pending = m_pendingBytes > 0;
    if (!pending || !m_progressPending || delivered > 0) m_progressOrigin = now;
    m_progressPending = pending;
    const double dtUs = static_cast<double>((now - m_lastRecord).GetMicroSeconds());
    const double dtSec = dtUs > 0.0 ? dtUs / 1.0e6 : 1.0e-6;
    const uint64_t lostBytes = m_pendingLostBytes;
    m_pendingLostBytes = 0;
    m_cumLostBytes += lostBytes;
    m_prevAck = ack;
    m_prevSnd = snd;
    m_lastRecord = now;

    const uint32_t unackedBytes = ForwardDelta(snd, ack);
    const double srttUs = m_srtt.GetMicroSeconds();
    const double rttFeature = srttUs / kRttUnitUs;
    const double rttVarFeature = m_rttvarUs / kRttVarUnitUs;
    const double rttRate =
        (srttUs > 0.0 && m_minRttUs > 0.0) ? (m_minRttUs / srttUs) : 0.0;
    const double thrBps = static_cast<double>(delivered) / dtSec;
    const double thrFeature = thrBps / kBytesPerSecPerMbps / kBwNormMbps;
    const double inflightFeature =
        (static_cast<double>(unackedBytes) / static_cast<double>(m_mss)) / kInflightUnitPkts;
    const double lostFeature =
        (static_cast<double>(m_cumLostBytes) / static_cast<double>(m_mss)) / kLostUnitPkts;
    m_lostNow = lostFeature;

    m_sentBytes.Add(static_cast<double>(sent));
    m_dlvBytes.Add(static_cast<double>(delivered));
    m_lossBytes.Add(static_cast<double>(lostBytes));
    m_unackedBytes.Add(static_cast<double>(unackedBytes));
    m_dtUs.Add(dtUs > 0.0 ? dtUs : 1.0);

    const double dtSumUs = m_dtUs.Sum(100);
    const double drMbps = dtSumUs > 0.0 ? (8.0 * m_dlvBytes.Sum(100)) / dtSumUs : 0.0;
    const double lossMbps = dtSumUs > 0.0 ? (8.0 * m_lossBytes.Sum(100)) / dtSumUs : 0.0;
    m_drMbps.Add(drMbps);
    const double drMaxMbps = m_drMbps.Max(kSageMid);

    const double sendBps = static_cast<double>(sent) / dtSec;
    m_sendBps.Add(sendBps);
    const double maxSendBps = m_sendBps.Max(5000);
    const double ackBps = static_cast<double>(delivered) / dtSec;
    const double ackedRate = maxSendBps > 0.0 ? ackBps / maxSendBps : 0.0;

    const double sentSum = m_sentBytes.Sum(100);
    const double unackedAvg = m_unackedBytes.Avg(100);
    const double cwndUnackedRate = sentSum > 0.0 ? unackedAvg / sentSum : unackedAvg;

    const double cwndBits = static_cast<double>(m_cwndBytes) * 8.0;
    const double bdpCwnd =
        (cwndBits > 0.0 && m_minRttUs > 0.0) ? (drMaxMbps * m_minRttUs) / cwndBits : 0.0;

    double preAct = 0.0;
    if (m_prevCwndBytes > 0 && m_cwndBytes > 0)
    {
        const double ratio =
            static_cast<double>(m_cwndBytes) / static_cast<double>(m_prevCwndBytes);
        preAct = std::round(std::log2(ratio) * 1000.0) / 1000.0;
    }

    m_rttHist.Add(rttFeature);
    m_thrHist.Add(thrFeature);
    m_rttRateHist.Add(rttRate);
    m_rttVarHist.Add(rttVarFeature);
    m_inflightHist.Add(inflightFeature);
    m_lostHist.Add(lostFeature);

    SageFeatures& f = m_tcp;
    ++f.observations;
    f.srtt = rttFeature;
    f.rttvar = rttVarFeature;
    f.thr = thrFeature;
    f.caState = static_cast<double>(m_caState);
    f.rtt = Summarize(m_rttHist);
    f.thrWin = Summarize(m_thrHist);
    f.rttRate = Summarize(m_rttRateHist);
    f.rttVar = Summarize(m_rttVarHist);
    f.inflight = Summarize(m_inflightHist);
    f.lost = Summarize(m_lostHist);
    f.timeDelta = m_minRttUs > 0.0 ? dtUs / m_minRttUs : 0.0;
    f.rttRateNow = rttRate;
    f.lossDb = lossMbps / kBwNormMbps;
    f.ackedRate = ackedRate;
    f.drRatio = m_prevDrMbps > 0.0 ? drMbps / m_prevDrMbps : drMbps;
    f.bdpCwnd = bdpCwnd;
    f.dr = drMbps / kBwNormMbps;
    f.cwndUnackedRate = cwndUnackedRate;
    f.drMax = drMaxMbps / kBwNormMbps;
    f.drMaxRatio = m_prevDrMaxMbps > 0.0 ? drMaxMbps / m_prevDrMaxMbps : drMaxMbps;
    f.preAct = preAct;

    m_prevDrMbps = drMbps;
    m_prevDrMaxMbps = drMaxMbps;
    if (m_cwndBytes > 0)
    {
        m_prevCwndBytes = m_cwndBytes;
    }
    WriteTcpRow(now, sent, delivered, lostBytes);
    m_rttUpdatesSincePoll = 0;
}

/** Count one generated application body and add its bytes to cumulative UE transmit totals.
 */
void
UeMetrics::NoteSend(uint32_t bytes)
{
    m_bytesSent += bytes;
    ++m_sent;
}

/** Count one delivered application reply and add its bytes to cumulative UE receive totals.
 */
void
UeMetrics::NoteRecv(uint32_t bytes)
{
    m_bytesReceived += bytes;
    ++m_received;
}

/** Return the cumulative generated-message count, also used as the next application sequence
 * number.
 */
uint64_t
UeMetrics::Sent() const
{
    return m_sent;
}

/** Snapshot application/TCP counters and derive missing replies, delivery ratio, and loss ratio
 * from the cumulative message counts.
 */
UeAppMetrics
UeMetrics::GetMetrics() const
{
    UeAppMetrics m;
    m.sent = m_sent;
    m.received = m_received;
    m.bytesSent = m_bytesSent;
    m.bytesReceived = m_bytesReceived;
    m.lost = (m_sent > m_received) ? (m_sent - m_received) : 0;
    m.lossRatio = (m_sent > 0) ? static_cast<double>(m.lost) / static_cast<double>(m_sent) : 0.0;
    m.deliveryRatio =
        (m_sent > 0) ? static_cast<double>(m_received) / static_cast<double>(m_sent) : 0.0;
    m.tcp = m_tcp;
    return m;
}

} // namespace ns3
