#include "metrics/direct-traffic.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/traffic-generator-helper.h"
#include "ns3/traffic-generator-ngmn-voip.h"
namespace ns3 {
void DirectTraffic::Install(Ptr<Node> source, Ptr<Node> receiver, Ipv4Address destination,
                            uint16_t port,std::string workload,std::string transport,
                            DataRate rate,uint32_t packetBytes,Time start,Time stop,const std::string& csv) {
    m_start=start;m_stop=stop;m_previous=start;m_workload=workload;
    const std::string protocol=transport=="udp"?"ns3::UdpSocketFactory":"ns3::TcpSocketFactory";
    PacketSinkHelper sink(protocol,InetSocketAddress(Ipv4Address::GetAny(),port));
    auto receiverApps=sink.Install(receiver);receiverApps.Start(Seconds(0));receiverApps.Stop(stop+Seconds(9));
    m_sink=DynamicCast<PacketSink>(receiverApps.Get(0));
    if(workload=="voip") {
        TrafficGeneratorHelper sender(protocol,InetSocketAddress(destination,port),TrafficGeneratorNgmnVoip::GetTypeId());
        m_source=sender.Install(source).Get(0);
    } else if(workload=="iperf" && transport=="tcp") {
        BulkSendHelper sender(protocol,InetSocketAddress(destination,port));
        sender.SetAttribute("SendSize",UintegerValue(packetBytes));sender.SetAttribute("MaxBytes",UintegerValue(0));
        m_source=DynamicCast<SourceApplication>(sender.Install(source).Get(0));
    } else {
        OnOffHelper sender(protocol,InetSocketAddress(destination,port));
        sender.SetConstantRate(rate,packetBytes);
        if(workload=="onoff") {
            sender.SetAttribute("OnTime",StringValue("ns3::ConstantRandomVariable[Constant=1]"));
            sender.SetAttribute("OffTime",StringValue("ns3::ConstantRandomVariable[Constant=1]"));
        }
        m_source=DynamicCast<SourceApplication>(sender.Install(source).Get(0));
    }
    m_source->SetStartTime(start);m_source->SetStopTime(stop);
    m_source->TraceConnectWithoutContext("Tx",MakeCallback(&DirectTraffic::Sent,this));
    m_sink->TraceConnectWithoutContext("Rx",MakeCallback(&DirectTraffic::Received,this));
    m_csv.open(csv);NS_ABORT_MSG_IF(!m_csv,"cannot open direct flow trace");
    m_csv<<"time_s,interval_s,tx_bytes,rx_bytes,goodput_Mbps\n";
    m_event=Simulator::Schedule(start+Seconds(1),&DirectTraffic::Tick,this);
}
void DirectTraffic::SetMetrics(const std::string& csv,Ptr<NrPhyMetricsCollector> phy,Time start,Time end) {
    m_metrics.OpenTcpTrace(csv);m_metrics.SetPhyMetrics(phy);m_metrics.SetRecordWindow(start,end);
    Simulator::Schedule(m_start+MilliSeconds(1),&DirectTraffic::Attach,this);
}
void DirectTraffic::Attach() {
    auto native=DynamicCast<SourceApplication>(m_source);
    auto voice=DynamicCast<TrafficGenerator>(m_source);
    auto socket=native?native->GetSocket():voice->GetSocket();
    if(socket) m_metrics.Start(socket);
    else if(Simulator::Now()<m_stop) Simulator::Schedule(MilliSeconds(10),&DirectTraffic::Attach,this);
}
void DirectTraffic::Sent(Ptr<const Packet> p) {m_tx+=p->GetSize();m_metrics.NoteSend(p->GetSize());}
void DirectTraffic::Received(Ptr<const Packet> p,const Address&) {m_rx+=p->GetSize();m_metrics.NoteRecv(p->GetSize());}
void DirectTraffic::Tick() {
    const auto now=Simulator::Now();const double dt=(now-m_previous).GetSeconds();
    if(dt>0) m_csv<<now.GetSeconds()<<','<<dt<<','<<m_tx<<','<<m_rx<<','<<(m_rx-m_lastRx)*8/dt/1e6<<'\n';
    m_previous=now;m_lastRx=m_rx;
    m_event=Simulator::Schedule(Seconds(1),&DirectTraffic::Tick,this);
}
UeAppMetrics DirectTraffic::GetMetrics() const {
    auto m=m_metrics.GetMetrics();m.success=m_tx>0 && m_rx>0;
    m.workloadLabel=m_workload;
    m.criterion="accepted_bytes>0 and delivered_bytes>0 (positive delivery, not completion or QoS)";
    // TCP stream segmentation makes source writes and sink read counts incomparable.
    m.deliveryRatio=m_tx?static_cast<double>(m_rx)/m_tx:0;
    m.lossRatio=std::max(0.0,1-m.deliveryRatio);m.lost=0;
    return m;
}
void DirectTraffic::Finish() {m_metrics.Stop();Simulator::Cancel(m_event);Tick();Simulator::Cancel(m_event);m_csv.flush();}
}
