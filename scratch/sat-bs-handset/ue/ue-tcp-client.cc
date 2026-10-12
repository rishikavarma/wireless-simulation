#include "ue/ue-tcp-client.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/tcp-socket-factory.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(UeTcpClient);

TypeId
UeTcpClient::GetTypeId()
{
    static TypeId tid = TypeId("ns3::UeTcpClient")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<UeTcpClient>();
    return tid;
}

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

void
UeTcpClient::SetTcpTrace(const std::string& path)
{
    m_metrics.OpenTcpTrace(path);
}

void
UeTcpClient::SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy)
{
    m_metrics.SetPhyMetrics(phy);
}

void
UeTcpClient::SetRecordWindow(Time start, Time end)
{
    m_metrics.SetRecordWindow(start, end);
}

bool
UeTcpClient::Connected() const
{
    return m_connected;
}

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

Time
UeTcpClient::RateInterval(uint32_t bytes) const
{
    const double bits = static_cast<double>(bytes * 8);
    return Seconds(bits / m_cfg.dataRate.GetBitRate());
}

Time
UeTcpClient::Exponential(double meanSeconds)
{
    NS_ABORT_MSG_IF(meanSeconds <= 0.0, "exponential mean must be > 0");
    return Seconds(m_exponential->GetValue(meanSeconds, 0.0));
}

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

Time
UeTcpClient::OffDuration()
{
    return Exponential(m_cfg.offTime.GetSeconds());
}

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

uint32_t
UeTcpClient::SampleIotSize()
{
    return m_uniform->GetInteger(m_cfg.iotPayloadMin, m_cfg.iotPayloadMax);
}

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

void
UeTcpClient::StartApplication()
{
    m_running = true;
    m_socket = Socket::CreateSocket(GetNode(), TcpSocketFactory::GetTypeId());
    EnableTcpNoDelay(m_socket);
    m_socket->Bind();
    m_socket->SetConnectCallback(MakeCallback(&UeTcpClient::ConnectionSucceeded, this),
                                 MakeCallback(&UeTcpClient::ConnectionFailed, this));
    m_socket->SetSendCallback(MakeCallback(&UeTcpClient::NotifySend, this));
    m_socket->SetRecvCallback(MakeCallback(&UeTcpClient::HandleReply, this));
    m_socket->Connect(InetSocketAddress(m_serverAddr, m_serverPort));
}

void
UeTcpClient::StopApplication()
{
    m_running = false;
    m_sending = false;
    m_sendBlocked = false;
    m_rpcInFlight = false;
    m_resume = SendResume::None;
    Simulator::Cancel(m_sendEvent);
    Simulator::Cancel(m_phaseEvent);
    m_metrics.Stop();
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
UeTcpClient::ConnectionSucceeded(Ptr<Socket> socket)
{
    if (!m_running)
    {
        return;
    }
    m_connected = true;
    m_socket = socket;
    m_metrics.Start(m_socket);
    StartWorkload();
}

void
UeTcpClient::ConnectionFailed(Ptr<Socket> /*socket*/)
{
    NS_ABORT_MSG("UE TCP connect to the base station failed");
}

void
UeTcpClient::NotifySend(Ptr<Socket> socket, uint32_t /*available*/)
{
    if (!m_running)
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

void
UeTcpClient::EnterOff()
{
    m_sending = false;
    Simulator::Cancel(m_sendEvent);
    m_phaseEvent = Simulator::Schedule(OffDuration(), &UeTcpClient::EnterOn, this);
}

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

void
UeTcpClient::HandleReply(Ptr<Socket> socket)
{
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
