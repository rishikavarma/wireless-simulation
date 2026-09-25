#include "satellite/sat-compute-server.h"

#include "ns3/inet-socket-address.h"
#include "ns3/ipv4-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(SatComputeServer);

TypeId
SatComputeServer::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SatComputeServer")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<SatComputeServer>();
    return tid;
}

void
SatComputeServer::Setup(uint16_t port, Time computeDelay)
{
    m_port = port;
    m_computeDelay = computeDelay;
}

uint64_t
SatComputeServer::GetRequests() const
{
    return m_requests;
}

uint64_t
SatComputeServer::GetReplies() const
{
    return m_replies;
}

void
SatComputeServer::StartApplication()
{
    m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    m_socket->Bind(InetSocketAddress(Ipv4Address::GetAny(), m_port));
    m_socket->SetRecvCallback(MakeCallback(&SatComputeServer::HandleRead, this));
}

void
SatComputeServer::StopApplication()
{
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
SatComputeServer::HandleRead(Ptr<Socket> socket)
{
    Address from;
    while (Ptr<Packet> packet = socket->RecvFrom(from))
    {
        ++m_requests;
        // Stand-in for on-board compute latency before the result is ready.
        Simulator::Schedule(m_computeDelay, &SatComputeServer::SendReply, this, packet, from);
    }
}

void
SatComputeServer::SendReply(Ptr<Packet> request, Address from)
{
    if (!m_socket)
    {
        return;
    }
    m_socket->SendTo(request->Copy(), 0, from);
    ++m_replies;
}

} // namespace ns3
