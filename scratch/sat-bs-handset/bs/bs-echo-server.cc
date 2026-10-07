#include "bs/bs-echo-server.h"

#include "common/app-protocol.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/ipv4-address.h"
#include "ns3/packet.h"
#include "ns3/tcp-socket-factory.h"

#include <algorithm>
#include <vector>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(BsEchoServer);

TypeId
BsEchoServer::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BsEchoServer")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<BsEchoServer>();
    return tid;
}

void
BsEchoServer::Setup(uint16_t port)
{
    m_port = port;
}

BsAppMetrics
BsEchoServer::GetMetrics() const
{
    return m_metrics.GetMetrics();
}

void
BsEchoServer::StartApplication()
{
    m_listen = Socket::CreateSocket(GetNode(), TcpSocketFactory::GetTypeId());
    EnableTcpNoDelay(m_listen);
    const int bound = m_listen->Bind(InetSocketAddress(Ipv4Address::GetAny(), m_port));
    NS_ABORT_MSG_IF(bound != 0, "BS TCP bind failed on port " << m_port);
    m_listen->SetAcceptCallback(MakeCallback(&BsEchoServer::HandleRequest, this),
                                MakeCallback(&BsEchoServer::HandleAccept, this));
    m_listen->Listen();
}

void
BsEchoServer::StopApplication()
{
    if (m_conn)
    {
        m_conn->Close();
        m_conn = nullptr;
    }
    if (m_listen)
    {
        m_listen->Close();
        m_listen = nullptr;
    }
}

bool
BsEchoServer::HandleRequest(Ptr<Socket> /*socket*/, const Address& /*from*/)
{
    return m_conn == nullptr;
}

void
BsEchoServer::HandleAccept(Ptr<Socket> socket, const Address& /*from*/)
{
    NS_ABORT_MSG_IF(m_conn, "BS TCP server accepts a single UE connection");
    m_conn = socket;
    EnableTcpNoDelay(m_conn);
    m_conn->SetRecvCallback(MakeCallback(&BsEchoServer::HandleRead, this));
    m_conn->SetSendCallback(MakeCallback(&BsEchoServer::NotifySend, this));
}

void
BsEchoServer::NotifySend(Ptr<Socket> socket, uint32_t /*available*/)
{
    m_pipe.Flush(socket);
}

void
BsEchoServer::HandleRead(Ptr<Socket> socket)
{
    while (Ptr<Packet> packet = socket->Recv())
    {
        m_pipe.Ingest(packet);
    }
    std::vector<uint8_t> body;
    while (m_pipe.Pop(body))
    {
        DispatchRequest(body);
    }
}

void
BsEchoServer::DispatchRequest(const std::vector<uint8_t>& body)
{
    NS_ABORT_MSG_IF(!m_conn, "BS TCP request with no connection");
    const uint32_t size = static_cast<uint32_t>(body.size());
    m_metrics.NoteRequest(size);

    std::vector<uint8_t> reply = body;
    AppHeader h;
    if (!ReadAppHeader(body.data(), size, h))
    {
        // Unknown payload: echo the message as-is.
    }
    else
    {
        const AppMsgType type = static_cast<AppMsgType>(h.msgType);
        switch (type)
        {
        case AppMsgType::FileChunk: {
            AppHeader ack = h;
            ack.msgType = static_cast<uint16_t>(AppMsgType::FileAck);
            reply.assign(kAppHeaderBytes, 0);
            WriteAppHeader(reply.data(), kAppHeaderBytes, ack);
            break;
        }
        case AppMsgType::RpcReq: {
            const uint32_t respSize = std::max(h.param0, kAppHeaderBytes);
            AppHeader resp = h;
            resp.msgType = static_cast<uint16_t>(AppMsgType::RpcResp);
            reply.assign(respSize, 0);
            WriteAppHeader(reply.data(), respSize, resp);
            break;
        }
        case AppMsgType::StreamChunk:
        case AppMsgType::IotReport:
        case AppMsgType::Probe:
        default:
            break;
        }
    }

    m_pipe.Enqueue(reply.data(), static_cast<uint32_t>(reply.size()));
    m_pipe.Flush(m_conn);
    m_metrics.NoteReply(static_cast<uint32_t>(reply.size()));
}

} // namespace ns3
