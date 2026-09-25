#include "bs/bs-offload-gateway.h"

#include "ns3/packet.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(BsOffloadGateway);

TypeId
BsOffloadGateway::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BsOffloadGateway")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<BsOffloadGateway>();
    return tid;
}

void
BsOffloadGateway::Setup(uint16_t localPort, Ipv4Address satAddr, uint16_t satPort)
{
    m_localPort = localPort;
    m_satAddr = satAddr;
    m_satPort = satPort;
}

uint64_t
BsOffloadGateway::GetUeRequests() const
{
    return m_ueRequests;
}

uint64_t
BsOffloadGateway::GetUeReplies() const
{
    return m_ueReplies;
}

void
BsOffloadGateway::StartApplication()
{
    m_ueSocket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    m_ueSocket->Bind(InetSocketAddress(Ipv4Address::GetAny(), m_localPort));
    m_ueSocket->SetRecvCallback(MakeCallback(&BsOffloadGateway::HandleUeRequest, this));

    m_satSocket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    m_satSocket->SetRecvCallback(MakeCallback(&BsOffloadGateway::HandleSatReply, this));
}

void
BsOffloadGateway::StopApplication()
{
    if (m_ueSocket)
    {
        m_ueSocket->Close();
        m_ueSocket = nullptr;
    }
    if (m_satSocket)
    {
        m_satSocket->Close();
        m_satSocket = nullptr;
    }
}

void
BsOffloadGateway::HandleUeRequest(Ptr<Socket> socket)
{
    Address from;
    while (Ptr<Packet> packet = socket->RecvFrom(from))
    {
        ++m_ueRequests;
        InetSocketAddress ueAddr = InetSocketAddress::ConvertFrom(from);
        // Remember who to send the sat reply back to (FIFO of return addresses).
        m_returnTo.push_back(ueAddr);
        m_satSocket->SendTo(packet, 0, InetSocketAddress(m_satAddr, m_satPort));
    }
}

void
BsOffloadGateway::HandleSatReply(Ptr<Socket> socket)
{
    Address from;
    while (Ptr<Packet> packet = socket->RecvFrom(from))
    {
        if (m_returnTo.empty())
        {
            continue;
        }
        InetSocketAddress ueAddr = m_returnTo.front();
        m_returnTo.pop_front();
        m_ueSocket->SendTo(packet, 0, ueAddr);
        ++m_ueReplies;
    }
}

} // namespace ns3
