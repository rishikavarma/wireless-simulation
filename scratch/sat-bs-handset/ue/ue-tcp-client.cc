#include "ue/ue-tcp-client.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/tcp-socket-factory.h"

#include <algorithm>
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

    NS_ABORT_MSG_IF(m_cfg.packetSize < kAppHeaderBytes,
                    "packetSize must be >= " << kAppHeaderBytes);
    if (m_cfg.workload == AppWorkload::File)
    {
        NS_ABORT_MSG_IF(m_cfg.fileSize == 0, "fileSize must be > 0");
        NS_ABORT_MSG_IF(m_cfg.packetSize <= kAppHeaderBytes, "file chunk packetSize must be > header");
    }
    if (m_cfg.workload == AppWorkload::Rpc)
    {
        NS_ABORT_MSG_IF(m_cfg.rpcReqSize < kAppHeaderBytes, "rpcReqSize too small");
        NS_ABORT_MSG_IF(m_cfg.rpcRespSize < kAppHeaderBytes, "rpcRespSize too small");
    }
    if (m_cfg.workload == AppWorkload::Stream)
    {
        NS_ABORT_MSG_IF(m_cfg.chunkSize < kAppHeaderBytes, "chunkSize too small");
    }
    if (m_cfg.workload == AppWorkload::Iot)
    {
        NS_ABORT_MSG_IF(m_cfg.iotPayload < kAppHeaderBytes, "iotPayload too small");
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
    m.rpcCompleted = m_rpcCompleted;
    switch (m_cfg.workload)
    {
    case AppWorkload::File:
        m.success = m_fileDone;
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

std::vector<uint8_t>
UeTcpClient::MakeBody(AppMsgType type,
                      uint64_t seq,
                      uint32_t totalSize,
                      uint32_t param0,
                      uint32_t param1)
{
    NS_ABORT_MSG_IF(totalSize < kAppHeaderBytes, "packet smaller than app header");
    std::vector<uint8_t> buf(totalSize, 0);
    AppHeader h;
    h.msgType = static_cast<uint16_t>(type);
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
                Simulator::Schedule(RateInterval(m_resumeBytes), &UeTcpClient::SendProbe, this);
        }
        break;
    case SendResume::File:
        if (!m_fileDone && m_fileNextChunk < m_fileChunksTotal)
        {
            m_sendEvent =
                Simulator::Schedule(RateInterval(m_resumeBytes), &UeTcpClient::SendFileChunk, this);
        }
        break;
    case SendResume::Stream:
        m_sendEvent = Simulator::Schedule(RateInterval(m_resumeBytes),
                                          &UeTcpClient::SendStreamChunk,
                                          this);
        break;
    case SendResume::Iot:
        m_sendEvent = Simulator::Schedule(m_cfg.iotInterval, &UeTcpClient::SendIot, this);
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
            EnterOn();
        }
        break;
    case AppWorkload::File: {
        const uint32_t payload = m_cfg.packetSize - kAppHeaderBytes;
        m_fileChunksTotal = (m_cfg.fileSize + payload - 1) / payload;
        SendFileChunk();
        break;
    }
    case AppWorkload::Rpc:
        SendRpc();
        break;
    case AppWorkload::Stream:
        SendStreamChunk();
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
    m_phaseEvent = Simulator::Schedule(m_cfg.onTime, &UeTcpClient::EnterOff, this);
}

void
UeTcpClient::EnterOff()
{
    m_sending = false;
    Simulator::Cancel(m_sendEvent);
    m_phaseEvent = Simulator::Schedule(m_cfg.offTime, &UeTcpClient::EnterOn, this);
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
    m_sendEvent = Simulator::Schedule(RateInterval(m_cfg.packetSize), &UeTcpClient::SendProbe, this);
}

void
UeTcpClient::SendFileChunk()
{
    if (!m_socket || !m_connected || m_fileDone || m_sendBlocked || m_fileNextChunk >= m_fileChunksTotal)
    {
        return;
    }
    const uint32_t payload = m_cfg.packetSize - kAppHeaderBytes;
    const uint64_t remaining = m_cfg.fileSize - m_fileBytesQueued;
    const uint32_t thisPayload = static_cast<uint32_t>(std::min<uint64_t>(payload, remaining));
    const uint32_t pktSize = kAppHeaderBytes + thisPayload;
    const uint64_t chunk = m_fileNextChunk;
    const std::vector<uint8_t> body = MakeBody(AppMsgType::FileChunk,
                                               chunk,
                                               pktSize,
                                               static_cast<uint32_t>(chunk),
                                               static_cast<uint32_t>(m_fileChunksTotal));
    m_fileBytesQueued += thisPayload;
    ++m_fileNextChunk;
    if (!Transmit(body, SendResume::File))
    {
        return;
    }
    if (m_fileNextChunk < m_fileChunksTotal)
    {
        m_sendEvent = Simulator::Schedule(RateInterval(pktSize), &UeTcpClient::SendFileChunk, this);
    }
}

void
UeTcpClient::SendRpc()
{
    if (!m_socket || !m_connected || m_rpcInFlight || m_sendBlocked)
    {
        return;
    }
    if (m_cfg.rpcCount > 0 && m_rpcCompleted >= m_cfg.rpcCount)
    {
        return;
    }
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body =
        MakeBody(AppMsgType::RpcReq, seq, m_cfg.rpcReqSize, m_cfg.rpcRespSize, 0);
    m_rpcInFlight = true;
    Transmit(body, SendResume::None);
}

void
UeTcpClient::SendStreamChunk()
{
    if (!m_socket || !m_connected || m_sendBlocked)
    {
        return;
    }
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body =
        MakeBody(AppMsgType::StreamChunk, seq, m_cfg.chunkSize, 0, 0);
    if (!Transmit(body, SendResume::Stream))
    {
        return;
    }
    m_sendEvent =
        Simulator::Schedule(RateInterval(m_cfg.chunkSize), &UeTcpClient::SendStreamChunk, this);
}

void
UeTcpClient::SendIot()
{
    if (!m_socket || !m_connected || m_sendBlocked)
    {
        return;
    }
    const uint64_t seq = m_metrics.Sent();
    const std::vector<uint8_t> body = MakeBody(AppMsgType::IotReport, seq, m_cfg.iotPayload, 0, 0);
    if (!Transmit(body, SendResume::Iot))
    {
        return;
    }
    m_sendEvent = Simulator::Schedule(m_cfg.iotInterval, &UeTcpClient::SendIot, this);
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
        m_fileAcked.insert(h.seq);
        if (!m_fileDone && m_fileAcked.size() >= m_fileChunksTotal)
        {
            m_fileDone = true;
            Simulator::Cancel(m_sendEvent);
            m_sendBlocked = false;
            m_resume = SendResume::None;
        }
    }

    if (type == AppMsgType::RpcResp && m_cfg.workload == AppWorkload::Rpc)
    {
        ++m_rpcCompleted;
        m_rpcInFlight = false;
        SendRpc();
    }
}

} // namespace ns3
