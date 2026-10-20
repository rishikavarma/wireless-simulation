#include "ue/ue-tcp-client.h"
#include "ns3/udp-socket-factory.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/tcp-socket-factory.h"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <vector>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(UeTcpClient);

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
UeTcpClient::GetTypeId()
{
    static TypeId tid = TypeId("ns3::UeTcpClient")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<UeTcpClient>();
    return tid;
}

/** Copy the server and workload configuration, allocate the workload random generators, and
 * validate the sizes and distributions needed by the selected application.
 */
void
UeTcpClient::Setup(Ipv4Address serverAddr, uint16_t serverPort, const UeWorkloadConfig& cfg)
{
    m_serverAddr = serverAddr;
    m_serverPort = serverPort;
    m_cfg = cfg;
    m_exponential = CreateObject<ExponentialRandomVariable>();
    m_lognormal = CreateObject<LogNormalRandomVariable>();
    m_pareto = CreateObject<ParetoRandomVariable>();
    m_uniform = CreateObject<UniformRandomVariable>();

    NS_ABORT_MSG_IF(m_cfg.packetSize < kAppHeaderBytes,
                    "packetSize must be >= " << kAppHeaderBytes);
    if (m_cfg.workload == AppWorkload::File)
    {
        NS_ABORT_MSG_IF(m_cfg.fileSizeSigma <= 0.0, "file size sigma must be > 0");
        NS_ABORT_MSG_IF(m_cfg.fileSizeMax == 0, "file size cap must be > 0");
        NS_ABORT_MSG_IF(m_cfg.fileReadingMean <= Time(0), "file reading time mean must be > 0");
        NS_ABORT_MSG_IF(m_cfg.packetSize <= kAppHeaderBytes, "file chunk packetSize must be > header");
    }
    if (m_cfg.workload == AppWorkload::Rpc)
    {
        NS_ABORT_MSG_IF(m_cfg.rpcReqSize < kAppHeaderBytes, "rpcReqSize too small");
        NS_ABORT_MSG_IF(m_cfg.rpcObjectScale < kAppHeaderBytes, "rpc object scale below header");
        NS_ABORT_MSG_IF(m_cfg.rpcObjectShape <= 0.0, "rpc object shape must be > 0");
        NS_ABORT_MSG_IF(m_cfg.rpcObjectMax < m_cfg.rpcObjectScale, "rpc object max below scale");
        NS_ABORT_MSG_IF(m_cfg.rpcObjectsMean <= 0.0, "rpc objects mean must be > 0");
        NS_ABORT_MSG_IF(m_cfg.rpcObjectGap <= Time(0) || m_cfg.rpcReadingMean <= Time(0),
                        "rpc gaps must be > 0");
    }
    if (m_cfg.workload == AppWorkload::Stream)
    {
        NS_ABORT_MSG_IF(m_cfg.frameInterval <= Time(0), "frame interval must be > 0");
        NS_ABORT_MSG_IF(m_cfg.slicesPerFrame == 0, "slices per frame must be > 0");
        NS_ABORT_MSG_IF(m_cfg.sliceScale <= 0.0 || m_cfg.sliceShape <= 0.0, "bad slice Pareto");
        NS_ABORT_MSG_IF(m_cfg.sliceMax < m_cfg.sliceScale, "slice max below scale");
        NS_ABORT_MSG_IF(m_cfg.sliceGapScale <= 0.0 || m_cfg.sliceGapShape <= 0.0, "bad slice gap");
        NS_ABORT_MSG_IF(m_cfg.sliceGapMax < m_cfg.sliceGapScale, "slice gap max below scale");
    }
    if (m_cfg.workload == AppWorkload::Iot)
    {
        NS_ABORT_MSG_IF(m_cfg.iotPayloadMin < kAppHeaderBytes, "iot payload min too small");
        NS_ABORT_MSG_IF(m_cfg.iotPayloadMax < m_cfg.iotPayloadMin, "iot payload max < min");
        NS_ABORT_MSG_IF(m_cfg.iotInterval <= Time(0), "iotInterval must be > 0");
    }
}

/** Open the TCP metric CSV through the UE collector before traffic begins.
 */
void
UeTcpClient::SetTcpTrace(const std::string& path)
{
    m_metrics.OpenTcpTrace(path);
}

/** Attach the shared PHY collector so TCP rows can include the most recently reported
 * downlink/uplink SINR.
 */
void
UeTcpClient::SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy)
{
    m_metrics.SetPhyMetrics(phy);
}

/** Pass the inclusive metric recording interval to the UE collector without changing application
 * start/stop times.
 */
void
UeTcpClient::SetRecordWindow(Time start, Time end)
{
    m_metrics.SetRecordWindow(start, end);
}

/** Return whether the current TCP socket has completed connection establishment.
 */
bool
UeTcpClient::Connected() const
{
    return m_connected;
}

/** Return whether any socket established a connection during this run, retaining the flag across
 * later reconnect failures.
 */
bool
UeTcpClient::EverConnected() const
{
    return m_everConnected;
}

/** Combine application/TCP counters with file/RPC progress and evaluate the selected workload
 * success criterion.
 */
UeAppMetrics
UeTcpClient::GetMetrics() const
{
    UeAppMetrics m = m_metrics.GetMetrics();
    m.workload = m_cfg.workload;
    m.fileChunks = m_fileChunksTotal;
    m.fileAcked = m_fileAcked.size();
    m.filesCompleted = m_filesCompleted;
    m.rpcCompleted = m_rpcCompleted;
    switch (m_cfg.workload)
    {
    case AppWorkload::File:
        m.success = m_filesCompleted > 0;
        break;
    case AppWorkload::Rpc:
        m.success = (m_rpcCompleted > 0) &&
                    (m_rpcCompleted >= 0.9 * std::max<uint64_t>(m.sent, 1));
        break;
    case AppWorkload::Stream:
    case AppWorkload::Iot:
    case AppWorkload::Probe:
        m.success = (m.sent > 0) && (m.received >= 0.9 * m.sent);
        break;
    }
    return m;
}

/** Convert a message byte count into its nominal pacing interval using the configured application
 * data rate.
 */
Time
UeTcpClient::RateInterval(uint32_t bytes) const
{
    const double bits = static_cast<double>(bytes * 8);
    return Seconds(bits / m_cfg.dataRate.GetBitRate());
}

/** Draw an unbounded exponential duration with the supplied positive mean in seconds.
 */
Time
UeTcpClient::Exponential(double meanSeconds)
{
    NS_ABORT_MSG_IF(meanSeconds <= 0.0, "exponential mean must be > 0");
    return Seconds(m_exponential->GetValue(meanSeconds, 0.0));
}

/** Use the nominal byte-rate interval for CBR/ON-OFF probes, or draw exponential interarrival times
 * for bursty probes.
 */
Time
UeTcpClient::ProbeGap(uint32_t bytes)
{
    const double mean = RateInterval(bytes).GetSeconds();
    if (m_cfg.trafficType == UeTrafficType::Bursty)
    {
        return Exponential(mean);
    }
    return Seconds(mean);
}

/** Draw an exponential ON duration for ON/OFF traffic or a Pareto duration scaled to the configured
 * mean for bursty traffic.
 */
Time
UeTcpClient::OnDuration()
{
    const double mean = m_cfg.onTime.GetSeconds();
    if (m_cfg.trafficType == UeTrafficType::Bursty)
    {
        const double shape = m_cfg.burstShape;
        const double scale = mean * (shape - 1.0) / shape;
        return Seconds(m_pareto->GetValue(scale, shape, 0.0));
    }
    return Exponential(mean);
}

/** Draw an exponential OFF duration using the configured mean idle time.
 */
Time
UeTcpClient::OffDuration()
{
    return Exponential(m_cfg.offTime.GetSeconds());
}

/** Reject lognormal draws outside one byte through the file cap; return the integer size or abort
 * after 100000 failed draws.
 */
uint64_t
UeTcpClient::SampleFileSize()
{
    for (int attempt = 0; attempt < 100000; ++attempt)
    {
        const double bytes = m_lognormal->GetValue(m_cfg.fileSizeMu, m_cfg.fileSizeSigma);
        if (bytes >= 1.0 && bytes <= static_cast<double>(m_cfg.fileSizeMax))
        {
            return static_cast<uint64_t>(bytes);
        }
    }
    NS_ABORT_MSG("file-size distribution produced no sample within the cap");
    return 0;
}

/** Draw from a bounded Pareto distribution, round to an integer, and enforce the caller minimum and
 * configured maximum.
 */
uint32_t
UeTcpClient::SampleParetoInt(double scale, double shape, double maxValue, uint32_t floor)
{
    const double draw = m_pareto->GetValue(scale, shape, maxValue);
    auto value = static_cast<uint32_t>(std::llround(draw));
    const auto cap = static_cast<uint32_t>(std::llround(maxValue));
    if (value < floor)
    {
        value = floor;
    }
    if (value > cap)
    {
        value = cap;
    }
    return value;
}

/** Sample one plus a geometric failure count using inverse transform sampling; rpcObjectsMean
 * describes the count before the added one.
 */
uint32_t
UeTcpClient::SampleObjectCount()
{
    const double p = 1.0 / (1.0 + m_cfg.rpcObjectsMean);
    double u = m_uniform->GetValue(0.0, 1.0);
    if (u < 1e-12)
    {
        u = 1e-12;
    }
    const double failures = std::floor(std::log(u) / std::log(1.0 - p));
    return static_cast<uint32_t>(failures) + 1;
}

/** Draw an integer report size uniformly between the configured inclusive payload bounds.
 */
uint32_t
UeTcpClient::SampleIotSize()
{
    return m_uniform->GetInteger(m_cfg.iotPayloadMin, m_cfg.iotPayloadMax);
}

/** Allocate a zero-filled body and write its type, sequence, simulation timestamp, flags, and
 * workload-specific parameters into the fixed header.
 */
std::vector<uint8_t>
UeTcpClient::MakeBody(AppMsgType type,
                      uint64_t seq,
                      uint32_t totalSize,
                                  uint32_t param0,
                                  uint32_t param1,
                                  uint16_t flags)
{
    NS_ABORT_MSG_IF(totalSize < kAppHeaderBytes, "packet smaller than app header");
    std::vector<uint8_t> buf(totalSize, 0);
    AppHeader h;
    h.msgType = static_cast<uint16_t>(type);
    h.flags = flags;
    h.seq = seq;
    h.sendTimeNs = static_cast<uint64_t>(Simulator::Now().GetNanoSeconds());
    h.param0 = param0;
    h.param1 = param1;
    WriteAppHeader(buf.data(), totalSize, h);
    return buf;
}

/** Count and frame a generated message, then flush it into TCP; remember the workload continuation
 * when socket backpressure leaves bytes queued.
 */
bool
UeTcpClient::Transmit(const std::vector<uint8_t>& body, SendResume resume)
{
    m_metrics.NoteSend(static_cast<uint32_t>(body.size()));
    m_pipe.Enqueue(body.data(), static_cast<uint32_t>(body.size()));
    if (m_pipe.Flush(m_socket))
    {
        return true;
    }
    m_sendBlocked = true;
    m_resume = resume;
    m_resumeBytes = static_cast<uint32_t>(body.size());
    return false;
}

/** Schedule the appropriate workload next-send event after a blocked frame drains, using that
 * workload pacing rule and current phase/progress.
 */
void
UeTcpClient::ScheduleResume()
{
    switch (m_resume)
    {
    case SendResume::Probe:
        if (m_sending)
        {
            m_sendEvent =
                Simulator::Schedule(ProbeGap(m_resumeBytes), &UeTcpClient::SendProbe, this);
        }
        break;
    case SendResume::File:
        if (!m_fileGapPending && m_fileNextChunk < m_fileChunksTotal)
        {
            m_sendEvent = Simulator::Schedule(Exponential(RateInterval(m_resumeBytes).GetSeconds()),
                                              &UeTcpClient::SendFileChunk,
                                              this);
        }
        break;
    case SendResume::Stream:
        if (m_slicesLeft > 0)
        {
            const Time gap = Seconds(m_pareto->GetValue(m_cfg.sliceGapScale,
                                                        m_cfg.sliceGapShape,
                                                        m_cfg.sliceGapMax));
            m_sendEvent = Simulator::Schedule(gap, &UeTcpClient::SendStreamChunk, this);
        }
        break;
    case SendResume::Iot:
        m_sendEvent = Simulator::Schedule(Exponential(m_cfg.iotInterval.GetSeconds()),
                                          &UeTcpClient::SendIot,
                                          this);
        break;
    case SendResume::None:
        break;
    }
    m_resume = SendResume::None;
}

/** Mark the client running and begin its first TCP connection attempt.
 */
void
UeTcpClient::StartApplication()
{
    m_running = true;
    OpenConnection();
}

/** Create a socket only while running and no socket exists, register connection/read/send/close
 * callbacks, and connect to the configured server.
 */
void
UeTcpClient::OpenConnection()
{
    if (!m_running || m_socket)
    {
        return;
    }
    m_socket = Socket::CreateSocket(GetNode(), m_cfg.transport == "udp" ? UdpSocketFactory::GetTypeId() : TcpSocketFactory::GetTypeId());
    EnableTcpNoDelay(m_socket);
    m_socket->Bind();
    m_socket->SetConnectCallback(MakeCallback(&UeTcpClient::ConnectionSucceeded, this),
                                 MakeCallback(&UeTcpClient::ConnectionFailed, this));
    m_socket->SetCloseCallbacks(MakeCallback(&UeTcpClient::ConnectionClosed, this),
                                MakeCallback(&UeTcpClient::ConnectionClosed, this));
    m_socket->SetSendCallback(MakeCallback(&UeTcpClient::NotifySend, this));
    m_socket->SetRecvCallback(MakeCallback(&UeTcpClient::HandleReply, this));
    m_socket->Connect(InetSocketAddress(m_serverAddr, m_serverPort));
    if (m_cfg.transport == "udp" && !m_connected) ConnectionSucceeded(m_socket);
}

/** Stop future retries and reuse the common connection teardown to stop metrics, cancel workload
 * events, clear buffers, and close the socket.
 */
void
UeTcpClient::StopApplication()
{
    m_running = false;
    Simulator::Cancel(m_retryEvent);
    ResetConnection();
}

/** Stop per-socket sampling, cancel workload events, clear transient workload/framing state, and
 * close the socket while retaining cumulative run counters.
 */
void
UeTcpClient::ResetConnection()
{
    m_metrics.Stop();
    m_connected = false;
    m_sending = false;
    m_sendBlocked = false;
    m_rpcInFlight = false;
    m_fileGapPending = false;
    m_resume = SendResume::None;
    m_slicesLeft = 0;
    Simulator::Cancel(m_sendEvent);
    Simulator::Cancel(m_phaseEvent);
    m_pipe = TcpPipe{};
    if (m_socket)
    {
        m_socket->SetCloseCallbacks(MakeNullCallback<void, Ptr<Socket>>(),
                                    MakeNullCallback<void, Ptr<Socket>>());
        m_socket->Close();
        m_socket = nullptr;
    }
}

/** Ignore stale callbacks; log loss of the active connection, tear it down, and schedule a new
 * connection attempt one second later.
 */
void
UeTcpClient::ConnectionClosed(Ptr<Socket> socket)
{
    if (!m_running || socket != m_socket)
    {
        return;
    }
    std::cout << "warn: UE TCP connection closed at t="
              << Simulator::Now().GetSeconds() << "s; retrying\n";
    ResetConnection();
    m_retryEvent = Simulator::Schedule(Seconds(1), &UeTcpClient::OpenConnection, this);
}

/** Accept success only for the active running socket, retain the ever-connected flag, start TCP
 * sampling, and launch the selected workload.
 */
void
UeTcpClient::ConnectionSucceeded(Ptr<Socket> socket)
{
    if (!m_running || socket != m_socket)
    {
        return;
    }
    m_connected = true;
    m_everConnected = true;
    m_metrics.Start(m_socket);
    StartWorkload();
}

/** Ignore stale failures; reset the current socket and schedule another attempt after one second
 * while retaining prior run history.
 */
void
UeTcpClient::ConnectionFailed(Ptr<Socket> socket)
{
    if (!m_running || socket != m_socket)
    {
        return;
    }
    std::cout << "warn: UE TCP connect/reconnect failed at t="
              << Simulator::Now().GetSeconds() << "s; retrying\n";
    ResetConnection();
    m_retryEvent = Simulator::Schedule(Seconds(1), &UeTcpClient::OpenConnection, this);
}

/** Flush queued bytes when the active socket becomes writable; resume workload pacing only after
 * the complete blocked frame has drained.
 */
void
UeTcpClient::NotifySend(Ptr<Socket> socket, uint32_t /*available*/)
{
    if (!m_running || !m_connected || socket != m_socket)
    {
        return;
    }
    if (!m_pipe.Flush(socket))
    {
        return;
    }
    if (!m_sendBlocked)
    {
        return;
    }
    m_sendBlocked = false;
    ScheduleResume();
}

/** Dispatch the selected application to its first probe phase, file transfer, RPC page, video
 * frame, or IoT report.
 */
void
UeTcpClient::StartWorkload()
{
    switch (m_cfg.workload)
    {
    case AppWorkload::Probe:
        if (m_cfg.trafficType == UeTrafficType::Cbr)
        {
            m_sending = true;
            SendProbe();
        }
        else
        {
            NS_ABORT_MSG_IF(m_cfg.onTime <= Time(0) || m_cfg.offTime <= Time(0),
                            "onoff/bursty requires onTime and offTime > 0");
            NS_ABORT_MSG_IF(m_cfg.trafficType == UeTrafficType::Bursty && m_cfg.burstShape <= 1.0,
                            "bursty Pareto shape must be > 1");
            EnterOn();
        }
        break;
    case AppWorkload::File:
        BeginFile();
        break;
    case AppWorkload::Rpc:
        StartRpcPage();
        break;
    case AppWorkload::Stream:
        StartFrame();
        break;
    case AppWorkload::Iot:
        SendIot();
        break;
    }
}

/** Enable probe generation on an established socket, send the first probe, and schedule the
 * transition to the next OFF phase.
 */
void
UeTcpClient::EnterOn()
{
    if (!m_socket || !m_connected)
    {
        return;
    }
    m_sending = true;
    SendProbe();
    m_phaseEvent = Simulator::Schedule(OnDuration(), &UeTcpClient::EnterOff, this);
}

/** Disable probes and cancel the outstanding send event, then schedule the next ON phase after a
 * sampled idle duration.
 */
void
UeTcpClient::EnterOff()
{
    m_sending = false;
    Simulator::Cancel(m_sendEvent);
    m_phaseEvent = Simulator::Schedule(OffDuration(), &UeTcpClient::EnterOn, this);
}

/** Generate one numbered probe when connected and unblocked, then schedule its next interarrival
 * unless TCP backpressure defers continuation.
 */
void
UeTcpClient::SendProbe()
{
    if (!m_socket || !m_connected || !m_sending || m_sendBlocked)
    {
        return;
    }
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body = MakeBody(AppMsgType::Probe, seq, m_cfg.packetSize, 0, 0);
    if (!Transmit(body, SendResume::Probe))
    {
        return;
    }
    m_sendEvent = Simulator::Schedule(ProbeGap(m_cfg.packetSize), &UeTcpClient::SendProbe, this);
}

/** Reset per-file ACK/progress state, sample the file size, compute its payload-chunk count,
 * advance the file ID, and send the first chunk.
 */
void
UeTcpClient::BeginFile()
{
    if (!m_running || !m_socket || !m_connected)
    {
        return;
    }
    const uint32_t payload = m_cfg.packetSize - kAppHeaderBytes;
    m_fileGapPending = false;
    m_fileAcked.clear();
    m_fileNextChunk = 0;
    m_fileBytesQueued = 0;
    m_fileSizeThis = SampleFileSize();
    m_fileChunksTotal = (m_fileSizeThis + payload - 1) / payload;
    ++m_fileId;
    SendFileChunk();
}

/** Queue the next bounded payload chunk with its index/count/file ID and pace further chunks
 * exponentially, pausing on socket backpressure.
 */
void
UeTcpClient::SendFileChunk()
{
    if (!m_socket || !m_connected || m_fileGapPending || m_sendBlocked ||
        m_fileNextChunk >= m_fileChunksTotal)
    {
        return;
    }
    const uint32_t payload = m_cfg.packetSize - kAppHeaderBytes;
    const uint64_t remaining = m_fileSizeThis - m_fileBytesQueued;
    const uint32_t thisPayload = static_cast<uint32_t>(std::min<uint64_t>(payload, remaining));
    const uint32_t pktSize = kAppHeaderBytes + thisPayload;
    const uint64_t chunk = m_fileNextChunk;
    const std::vector<uint8_t> body = MakeBody(AppMsgType::FileChunk,
                                               chunk,
                                               pktSize,
                                               static_cast<uint32_t>(chunk),
                                               static_cast<uint32_t>(m_fileChunksTotal),
                                               m_fileId);
    m_fileBytesQueued += thisPayload;
    ++m_fileNextChunk;
    if (!Transmit(body, SendResume::File))
    {
        return;
    }
    if (m_fileNextChunk < m_fileChunksTotal)
    {
        m_sendEvent = Simulator::Schedule(Exponential(RateInterval(pktSize).GetSeconds()),
                                          &UeTcpClient::SendFileChunk,
                                          this);
    }
}

/** Start a page while running and below the RPC count limit, sample its object count, and request
 * its first object.
 */
void
UeTcpClient::StartRpcPage()
{
    if (!m_running || !m_socket || !m_connected)
    {
        return;
    }
    if (m_cfg.rpcCount > 0 && m_rpcCompleted >= m_cfg.rpcCount)
    {
        return;
    }
    m_rpcObjectsLeft = SampleObjectCount();
    SendRpc();
}

/** Request one sampled-size RPC object only when none is in flight; decrement page progress and
 * wait for its response before requesting another.
 */
void
UeTcpClient::SendRpc()
{
    if (!m_socket || !m_connected || m_rpcInFlight || m_sendBlocked || m_rpcObjectsLeft == 0)
    {
        return;
    }
    if (m_cfg.rpcCount > 0 && m_rpcCompleted >= m_cfg.rpcCount)
    {
        return;
    }
    const uint32_t respSize = SampleParetoInt(m_cfg.rpcObjectScale,
                                               m_cfg.rpcObjectShape,
                                               m_cfg.rpcObjectMax,
                                               kAppHeaderBytes);
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body =
        MakeBody(AppMsgType::RpcReq, seq, m_cfg.rpcReqSize, respSize, 0);
    --m_rpcObjectsLeft;
    m_rpcInFlight = true;
    Transmit(body, SendResume::None);
}

/** Cancel leftover slice scheduling, reset the frame slice count, send its first slice, and
 * schedule the next fixed-interval frame.
 */
void
UeTcpClient::StartFrame()
{
    if (!m_running || !m_socket || !m_connected)
    {
        return;
    }
    Simulator::Cancel(m_sendEvent);
    m_slicesLeft = m_cfg.slicesPerFrame;
    SendStreamChunk();
    m_phaseEvent = Simulator::Schedule(m_cfg.frameInterval, &UeTcpClient::StartFrame, this);
}

/** Send one Pareto-sized frame slice, then schedule remaining slices with bounded Pareto gaps
 * unless TCP backpressure pauses generation.
 */
void
UeTcpClient::SendStreamChunk()
{
    if (!m_socket || !m_connected || m_sendBlocked || m_slicesLeft == 0)
    {
        return;
    }
    const uint32_t payload = SampleParetoInt(m_cfg.sliceScale,
                                              m_cfg.sliceShape,
                                              m_cfg.sliceMax,
                                              1);
    const uint32_t pktSize = kAppHeaderBytes + payload;
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body = MakeBody(AppMsgType::StreamChunk, seq, pktSize, 0, 0);
    --m_slicesLeft;
    if (!Transmit(body, SendResume::Stream))
    {
        return;
    }
    if (m_slicesLeft > 0)
    {
        const Time gap = Seconds(
            m_pareto->GetValue(m_cfg.sliceGapScale, m_cfg.sliceGapShape, m_cfg.sliceGapMax));
        m_sendEvent = Simulator::Schedule(gap, &UeTcpClient::SendStreamChunk, this);
    }
}

/** Queue a uniformly sized numbered report and schedule the next report with an exponential
 * interarrival time, deferring when TCP is blocked.
 */
void
UeTcpClient::SendIot()
{
    if (!m_socket || !m_connected || m_sendBlocked)
    {
        return;
    }
    const uint32_t pktSize = SampleIotSize();
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body = MakeBody(AppMsgType::IotReport, seq, pktSize, 0, 0);
    if (!Transmit(body, SendResume::Iot))
    {
        return;
    }
    m_sendEvent =
        Simulator::Schedule(Exponential(m_cfg.iotInterval.GetSeconds()), &UeTcpClient::SendIot, this);
}

/** Drain the active socket into the framing pipe and dispatch all complete reply bodies while the
 * client is running.
 */
void
UeTcpClient::HandleReply(Ptr<Socket> socket)
{
    if (!m_running || socket != m_socket)
    {
        return;
    }
    while (Ptr<Packet> packet = socket->Recv())
    {
        m_pipe.Ingest(packet);
    }
    std::vector<uint8_t> body;
    while (m_pipe.Pop(body))
    {
        DispatchReply(body);
    }
}

/** Count a decoded reply, track unique ACKs for the current file, and schedule file reading pauses
 * or RPC object/page continuation as responses complete.
 */
void
UeTcpClient::DispatchReply(const std::vector<uint8_t>& body)
{
    AppHeader h;
    if (!ReadAppHeader(body.data(), static_cast<uint32_t>(body.size()), h))
    {
        return;
    }

    const AppMsgType type = static_cast<AppMsgType>(h.msgType);
    m_metrics.NoteRecv(static_cast<uint32_t>(body.size()));

    if (type == AppMsgType::FileAck && m_cfg.workload == AppWorkload::File)
    {
        if (h.flags != m_fileId || m_fileGapPending)
        {
            return;
        }
        m_fileAcked.insert(h.seq);
        if (m_fileAcked.size() >= m_fileChunksTotal)
        {
            ++m_filesCompleted;
            m_fileGapPending = true;
            Simulator::Cancel(m_sendEvent);
            m_sendBlocked = false;
            m_resume = SendResume::None;
            m_phaseEvent = Simulator::Schedule(Exponential(m_cfg.fileReadingMean.GetSeconds()),
                                              &UeTcpClient::BeginFile,
                                              this);
        }
    }

    if (type == AppMsgType::RpcResp && m_cfg.workload == AppWorkload::Rpc)
    {
        ++m_rpcCompleted;
        m_rpcInFlight = false;
        if (!m_running || (m_cfg.rpcCount > 0 && m_rpcCompleted >= m_cfg.rpcCount))
        {
            return;
        }
        if (m_rpcObjectsLeft > 0)
        {
            m_phaseEvent = Simulator::Schedule(Exponential(m_cfg.rpcObjectGap.GetSeconds()),
                                              &UeTcpClient::SendRpc,
                                              this);
        }
        else
        {
            m_phaseEvent = Simulator::Schedule(Exponential(m_cfg.rpcReadingMean.GetSeconds()),
                                              &UeTcpClient::StartRpcPage,
                                              this);
        }
    }
}

} // namespace ns3
