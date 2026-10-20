#include "bs/bs-echo-server.h"
#include "ns3/udp-socket-factory.h"

#include "common/app-protocol.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/ipv4-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/tcp-socket-factory.h"

#include <algorithm>
#include <vector>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(BsEchoServer);

/** Register the ns-3 runtime type, parent, and constructor through a lazily initialized TypeId.
 */
TypeId
BsEchoServer::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BsEchoServer")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<BsEchoServer>();
    return tid;
}

/** Store the listening port and server compute-delay setting for use when the application starts.
 */
void
BsEchoServer::Setup(uint16_t port, uint64_t computeDelayUs, bool udp)
{
    m_udp = udp;
    m_port = port;
    m_computeDelayUs = computeDelayUs;
}

/** Return a snapshot of application request/reply counters from the server metric collector.
 */
BsAppMetrics
BsEchoServer::GetMetrics() const
{
    return m_metrics.GetMetrics();
}

/** Create and bind the listening TCP socket, enable listening, and register accept callbacks for
 * the single UE connection.
 */
void
BsEchoServer::StartApplication()
{
    m_listen = Socket::CreateSocket(GetNode(), m_udp ? UdpSocketFactory::GetTypeId() : TcpSocketFactory::GetTypeId());
    EnableTcpNoDelay(m_listen);
    const int bound = m_listen->Bind(InetSocketAddress(Ipv4Address::GetAny(), m_port));
    NS_ABORT_MSG_IF(bound != 0, "BS TCP bind failed on port " << m_port);
    if (m_udp)
    {
        m_conn = m_listen;
        m_conn->SetRecvCallback(MakeCallback(&BsEchoServer::HandleRead, this));
        return;
    }
    m_listen->SetAcceptCallback(MakeCallback(&BsEchoServer::HandleRequest, this),
                                MakeCallback(&BsEchoServer::HandleAccept, this));
    m_listen->Listen();
}

/** Disable connection-close callbacks and close the accepted and listening sockets at application
 * shutdown.
 */
void
BsEchoServer::StopApplication()
{
    if (m_conn)
    {
        m_conn->SetCloseCallbacks(MakeNullCallback<void, Ptr<Socket>>(),
                                  MakeNullCallback<void, Ptr<Socket>>());
        m_conn->Close();
        m_conn = nullptr;
    }
    if (m_listen)
    {
        m_listen->Close();
        m_listen = nullptr;
    }
}

/** Accept a connection request only when the single active connection slot is free.
 */
bool
BsEchoServer::HandleRequest(Ptr<Socket> /*socket*/, const Address& /*from*/)
{
    return m_conn == nullptr;
}

/** Install the accepted socket, clear framing state, disable Nagle, and attach read, send-ready,
 * and close callbacks.
 */
void
BsEchoServer::HandleAccept(Ptr<Socket> socket, const Address& /*from*/)
{
    NS_ABORT_MSG_IF(m_conn, "BS TCP server accepts a single UE connection");
    m_conn = socket;
    m_pipe = TcpPipe{};
    EnableTcpNoDelay(m_conn);
    m_conn->SetCloseCallbacks(MakeCallback(&BsEchoServer::HandleClose, this),
                              MakeCallback(&BsEchoServer::HandleClose, this));
    m_conn->SetRecvCallback(MakeCallback(&BsEchoServer::HandleRead, this));
    m_conn->SetSendCallback(MakeCallback(&BsEchoServer::NotifySend, this));
}

/** Ignore stale sockets; close the current connection without recursive callbacks and clear its
 * queued framing bytes.
 */
void
BsEchoServer::HandleClose(Ptr<Socket> socket)
{
    if (socket != m_conn)
    {
        return;
    }
    m_conn->SetCloseCallbacks(MakeNullCallback<void, Ptr<Socket>>(),
                              MakeNullCallback<void, Ptr<Socket>>());
    m_conn->Close();
    m_conn = nullptr;
    m_pipe = TcpPipe{};
}

/** Flush queued reply bytes when the active socket reports available transmit space.
 */
void
BsEchoServer::NotifySend(Ptr<Socket> socket, uint32_t /*available*/)
{
    if (socket == m_conn)
    {
        m_pipe.Flush(socket);
    }
}

/** Drain available TCP packets into the byte pipe, extract every complete request frame, and
 * dispatch each body.
 */
void
BsEchoServer::HandleRead(Ptr<Socket> socket)
{
    if (socket != m_conn)
    {
        return;
    }
    Address from;
    while (Ptr<Packet> packet = socket->RecvFrom(from))
    {
        if (m_udp) socket->Connect(from);
        m_pipe.Ingest(packet);
    }
    std::vector<uint8_t> body;
    while (m_pipe.Pop(body))
    {
        DispatchRequest(body);
    }
}

/** Count a request and build its workload response: header-only file ACK, requested-size RPC
 * object, or echo; send immediately or after configured compute delay.
 */
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

    if (m_computeDelayUs == 0)
    {
        SendReply(m_conn, std::move(reply));
        return;
    }
    Simulator::Schedule(MicroSeconds(m_computeDelayUs),
                        &BsEchoServer::SendReply,
                        this,
                        m_conn,
                        reply);
}

/** Discard replies targeting a closed/replaced socket; otherwise frame and queue the reply, flush
 * available bytes, and count it as submitted.
 */
void
BsEchoServer::SendReply(Ptr<Socket> socket, std::vector<uint8_t> reply)
{
    if (!socket || socket != m_conn)
    {
        return;
    }
    m_pipe.Enqueue(reply.data(), static_cast<uint32_t>(reply.size()));
    m_pipe.Flush(m_conn);
    m_metrics.NoteReply(static_cast<uint32_t>(reply.size()));
}

} // namespace ns3
