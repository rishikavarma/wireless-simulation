#include "ue/ue-offload-client.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(UeOffloadClient);

UeTrafficType
ParseUeTrafficType(const std::string& name)
{
    if (name == "cbr")
    {
        return UeTrafficType::Cbr;
    }
    if (name == "onoff")
    {
        return UeTrafficType::OnOff;
    }
    if (name == "bursty")
    {
        return UeTrafficType::Bursty;
    }
    NS_ABORT_MSG("Unknown --traffic value '" << name << "' (try cbr, onoff, or bursty)");
    return UeTrafficType::Cbr;
}

std::string
UeTrafficTypeToString(UeTrafficType type)
{
    switch (type)
    {
    case UeTrafficType::Cbr:
        return "cbr";
    case UeTrafficType::OnOff:
        return "onoff";
    case UeTrafficType::Bursty:
        return "bursty";
    }
    return "unknown";
}

TypeId
UeOffloadClient::GetTypeId()
{
    static TypeId tid = TypeId("ns3::UeOffloadClient")
                            .SetParent<Application>()
                            .SetGroupName("Scratch")
                            .AddConstructor<UeOffloadClient>();
    return tid;
}

void
UeOffloadClient::Setup(Ipv4Address gatewayAddr,
                       uint16_t gatewayPort,
                       uint32_t packetSize,
                       DataRate dataRate,
                       UeTrafficType trafficType,
                       Time onTime,
                       Time offTime)
{
    m_gatewayAddr = gatewayAddr;
    m_gatewayPort = gatewayPort;
    m_packetSize = packetSize;
    m_dataRate = dataRate;
    m_trafficType = trafficType;
    m_onTime = onTime;
    m_offTime = offTime;
}

uint64_t
UeOffloadClient::GetSent() const
{
    return m_sent;
}

uint64_t
UeOffloadClient::GetReceived() const
{
    return m_received;
}

Time
UeOffloadClient::PacketInterval() const
{
    const double bits = static_cast<double>(m_packetSize * 8);
    return Seconds(bits / m_dataRate.GetBitRate());
}

void
UeOffloadClient::StartApplication()
{
    m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    m_socket->Bind();
    m_socket->SetRecvCallback(MakeCallback(&UeOffloadClient::HandleReply, this));

    if (m_trafficType == UeTrafficType::Cbr)
    {
        m_sending = true;
        SendNext();
        return;
    }

    NS_ABORT_MSG_IF(m_onTime <= Time(0) || m_offTime <= Time(0),
                    "onoff/bursty traffic requires --onTime and --offTime > 0");
    EnterOn();
}

void
UeOffloadClient::StopApplication()
{
    Simulator::Cancel(m_sendEvent);
    Simulator::Cancel(m_phaseEvent);
    m_sending = false;
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
UeOffloadClient::EnterOn()
{
    if (!m_socket)
    {
        return;
    }
    m_sending = true;
    SendNext();
    m_phaseEvent = Simulator::Schedule(m_onTime, &UeOffloadClient::EnterOff, this);
}

void
UeOffloadClient::EnterOff()
{
    m_sending = false;
    Simulator::Cancel(m_sendEvent);
    m_phaseEvent = Simulator::Schedule(m_offTime, &UeOffloadClient::EnterOn, this);
}

void
UeOffloadClient::SendNext()
{
    if (!m_socket || !m_sending)
    {
        return;
    }
    Ptr<Packet> packet = Create<Packet>(m_packetSize);
    m_socket->SendTo(packet, 0, InetSocketAddress(m_gatewayAddr, m_gatewayPort));
    ++m_sent;
    m_sendEvent = Simulator::Schedule(PacketInterval(), &UeOffloadClient::SendNext, this);
}

void
UeOffloadClient::HandleReply(Ptr<Socket> socket)
{
    Address from;
    while (socket->RecvFrom(from))
    {
        ++m_received;
    }
}

} // namespace ns3
