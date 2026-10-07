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

uint32_t
ForwardDelta(uint32_t now, uint32_t prev)
{
    const uint32_t delta = now - prev;
    return delta > 0x80000000u ? 0u : delta;
}

void
AppendWindowHeader(std::ostream& os, const char* name)
{
    os << ',' << name << "_s_avg," << name << "_s_min," << name << "_s_max," << name
       << "_m_avg," << name << "_m_min," << name << "_m_max," << name << "_l_avg," << name
       << "_l_min," << name << "_l_max";
}

void
AppendWindow(std::ostream& os, const SageWindow& window)
{
    os << ',' << window.sAvg << ',' << window.sMin << ',' << window.sMax << ',' << window.mAvg
       << ',' << window.mMin << ',' << window.mMax << ',' << window.lAvg << ',' << window.lMin
       << ',' << window.lMax;
}

void
Assign(SageWindow& window, double sAvg, double sMin, double sMax, double mAvg, double mMin,
       double mMax, double lAvg, double lMin, double lMax)
{
    window.sAvg = sAvg;
    window.sMin = sMin;
    window.sMax = sMax;
    window.mAvg = mAvg;
    window.mMin = mMin;
    window.mMax = mMax;
    window.lAvg = lAvg;
    window.lMin = lMin;
    window.lMax = lMax;
}

} // namespace

UeMetrics::History::History(std::size_t cap)
    : m_cap(cap)
{
}

void
UeMetrics::History::Add(double value)
{
    m_q.push_back(value);
    if (m_q.size() > m_cap)
    {
        m_q.pop_front();
    }
}

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

SageWindow
UeMetrics::Summarize(const History& history)
{
    SageWindow window;
    Assign(window,
           history.Avg(kSageShort),
           history.Min(kSageShort),
           history.Max(kSageShort),
           history.Avg(kSageMid),
           history.Min(kSageMid),
           history.Max(kSageMid),
           history.Avg(kSageLong),
           history.Min(kSageLong),
           history.Max(kSageLong));
    return window;
}

void
UeMetrics::Start(Ptr<Socket> socket)
{
    m_socket = socket;
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

void
UeMetrics::OpenTcpTrace(const std::string& path)
{
    m_tcpTrace.open(path);
    NS_ABORT_MSG_IF(!m_tcpTrace, "cannot open TCP trace " << path);
    m_tcpTrace << std::setprecision(8);
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
                  "cwnd_unacked_rate,dr_max,dr_max_ratio,pre_act,sinr_dl_db,sinr_ul_db\n";
}

void
UeMetrics::SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy)
{
    m_phy = phy;
}

void
UeMetrics::SetRecordWindow(Time start, Time end)
{
    m_limitRecord = true;
    m_recordStart = start;
    m_recordEnd = end;
}

bool
UeMetrics::Recording(Time now) const
{
    return !m_limitRecord || (now >= m_recordStart && now <= m_recordEnd);
}

void
UeMetrics::WriteTcpRow(Time now)
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
    m_tcpTrace << '\n';
}

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
    if (m_tcpTrace.is_open())
    {
        m_tcpTrace.flush();
    }
}

void
UeMetrics::OnSrtt(Time /*oldRtt*/, Time newRtt)
{
    m_srtt = newRtt;
}

void
UeMetrics::OnLastRtt(Time /*oldRtt*/, Time newRtt)
{
    const double sampleUs = newRtt.GetMicroSeconds();
    if (!(sampleUs > 0.0))
    {
        return;
    }
    if (!(m_minRttUs > 0.0) || sampleUs < m_minRttUs)
    {
        m_minRttUs = sampleUs;
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

void
UeMetrics::OnCwnd(uint32_t /*oldCwnd*/, uint32_t newCwnd)
{
    m_cwndBytes = newCwnd;
}

void
UeMetrics::OnCaState(TcpSocketState::TcpCongState_t /*oldState*/,
                         TcpSocketState::TcpCongState_t newState)
{
    m_caState = static_cast<uint8_t>(newState);
}

void
UeMetrics::OnHighestSequence(SequenceNumber32 /*oldSeq*/, SequenceNumber32 newSeq)
{
    m_highTx = newSeq.GetValue();
    m_haveTx = true;
}

void
UeMetrics::Poll()
{
    if (!m_running)
    {
        return;
    }
    Commit();
    m_event = Simulator::Schedule(kSamplePeriod, &UeMetrics::Poll, this);
}

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
    WriteTcpRow(now);
}

void
UeMetrics::NoteSend(uint32_t bytes)
{
    m_bytesSent += bytes;
    ++m_sent;
}

void
UeMetrics::NoteRecv(uint32_t bytes)
{
    m_bytesReceived += bytes;
    ++m_received;
}

uint64_t
UeMetrics::Sent() const
{
    return m_sent;
}

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
