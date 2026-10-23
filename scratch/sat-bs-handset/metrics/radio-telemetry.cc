#include "metrics/radio-telemetry.h"
#include "ns3/core-module.h"
#include "ns3/nr-spectrum-phy.h"
#include "ns3/nr-pdcp-header.h"
#include "ns3/nr-pdcp-tag.h"
#include "ns3/nr-rlc-am-header.h"
#include "ns3/nr-radio-bearer-info.h"
#include <cmath>
#include <iomanip>
#include <limits>
namespace ns3 {
void RadioTelemetry::Connect(const MetricOutputPaths& paths, const ScenarioConfig& sim,
                             NetDeviceContainer ues, NetDeviceContainer gnbs) {
    m_ues = ues; m_period = Seconds(sim.kpiIntervalS); m_stop = Seconds(sim.simTime);
    const auto stem = paths.directory + "/" + sim.outputPrefix;
    m_events.open(stem + "-radio-events.csv"); m_tb.open(stem + "-transport-blocks.csv");
    m_kpi.open(stem + "-kpis.csv");
    if (sim.payloadTraces) m_payload.open(stem + "-mac-payloads.csv");
    NS_ABORT_MSG_IF(!m_events || !m_tb || !m_kpi, "cannot open radio telemetry");
    m_events << "time_s,event,direction,rnti,lcid,sequence_number,size_bytes,value,details\n";
    m_tb << "time_s,event,direction,rnti,tb_bytes,mac_bytes,mcs,rv,ndi,harq_id,prbs,num_symbols,sinr_db,tbler,crc_pass_model\n";
    m_kpi << "time_s,interval_s,ue_index,rnti,DRB.PdcpSduVolumeDL_kB,DRB.PdcpSduVolumeUL_kB,DRB.RlcSduDelayDl_us,RRU.PrbTotDl,RRU.PrbTotUl,DRB.UEThpDl_Mbps,DRB.UEThpUl_Mbps,dl_prb_symbols,ul_prb_symbols,dl_delay_samples,rlc_tx_drop_dl_pdus,rlc_tx_drop_ul_pdus,rlc_tx_drop_dl_bytes,rlc_tx_drop_ul_bytes,rnti_history\n";
    if (m_payload) m_payload << "time_s,event,direction,rnti,packet_uid,size_bytes,hex\n";
    for (auto* f : {&m_events,&m_tb,&m_kpi,&m_payload}) *f << std::setprecision(12);
    for (uint32_t i=0; i<ues.GetN(); ++i) {
        auto dev = DynamicCast<NrUeNetDevice>(ues.Get(i));
        m_imsiUe[dev->GetImsi()] = i;
        auto rrc = dev->GetRrc();
        for (const auto& name : {"RandomAccessSuccessful","RandomAccessError","ConnectionEstablished",
                                "ConnectionReconfiguration","HandoverEndOk","HandoverEndError","Sib2Received","RadioLinkFailure"})
            rrc->TraceConnectWithoutContext(name, MakeCallback(&RadioTelemetry::Rrc,this).Bind(std::string(name)));
        rrc->TraceConnectWithoutContext("DrbCreated", MakeCallback(&RadioTelemetry::Drb,this));
        for (uint8_t b=0;b<2;++b) {
            auto phy=NrHelper::GetUePhy(dev,b)->GetSpectrumPhy();
            phy->TraceConnectWithoutContext("TxTransportBlock", MakeCallback(&RadioTelemetry::Tx,this).Bind(false,
                NrHelper::GetGnbMac(gnbs.Get(0),b)->GetNumRbPerRbg(),NrHelper::GetGnbPhy(gnbs.Get(0),b)->GetRbNum()));
            phy->TraceConnectWithoutContext("RxPacketTraceUe",MakeCallback(&RadioTelemetry::Rx,this).Bind(true));
            phy->TraceConnectWithoutContext("RxMacPdu",MakeCallback(&RadioTelemetry::MacRx,this).Bind(true));
            phy->TraceConnectWithoutContext("NtnBoundaryOverlap",MakeCallback(&RadioTelemetry::Boundary,this).Bind(true));
            phy->TraceConnectWithoutContext("HarqDecision",MakeCallback(&RadioTelemetry::Harq,this).Bind(true));
            auto mac=NrHelper::GetUeMac(dev,b);
            mac->TraceConnectWithoutContext("ConnectedRaMsg3",MakeCallback(&RadioTelemetry::ConnectedRaMsg3,this));
            mac->TraceConnectWithoutContext("UeMacTxedCtrlMsgsTrace",MakeCallback(&RadioTelemetry::Control,this).Bind(std::string("UE_CTRL_TX")));
            mac->TraceConnectWithoutContext("UeMacRxedCtrlMsgsTrace",MakeCallback(&RadioTelemetry::Control,this).Bind(std::string("UE_CTRL_RX")));
        }
    }
    for (uint8_t b=0;b<2;++b) {
        NrHelper::GetGnbMac(gnbs.Get(0),b)->TraceConnectWithoutContext("ConnectedRaIdentity",
            MakeCallback(&RadioTelemetry::ConnectedRa,this));
        auto phy=NrHelper::GetGnbPhy(gnbs.Get(0),b)->GetSpectrumPhy();
        phy->TraceConnectWithoutContext("TxTransportBlock",MakeCallback(&RadioTelemetry::Tx,this).Bind(true,
            NrHelper::GetGnbMac(gnbs.Get(0),b)->GetNumRbPerRbg(),NrHelper::GetGnbPhy(gnbs.Get(0),b)->GetRbNum()));
        phy->TraceConnectWithoutContext("RxPacketTraceGnb",MakeCallback(&RadioTelemetry::Rx,this).Bind(false));
        phy->TraceConnectWithoutContext("RxMacPdu",MakeCallback(&RadioTelemetry::MacRx,this).Bind(false));
        phy->TraceConnectWithoutContext("NtnBoundaryOverlap",MakeCallback(&RadioTelemetry::Boundary,this).Bind(false));
        phy->TraceConnectWithoutContext("HarqDecision",MakeCallback(&RadioTelemetry::Harq,this).Bind(false));
    }
    m_tick=Simulator::Schedule(m_period,&RadioTelemetry::Tick,this);
}
void RadioTelemetry::Drb(uint64_t, uint16_t, uint16_t, uint8_t) {
    Simulator::ScheduleNow(&RadioTelemetry::Discover,this);
}
void RadioTelemetry::Discover() {
    for (bool ue : {false,true}) for (bool pdcp : {false,true}) {
        const std::string base=ue ? "/NodeList/*/DeviceList/*/NrUeRrc" : "/NodeList/*/DeviceList/*/NrGnbRrc/UeMap/*";
        auto matches=Config::LookupMatches(base+"/DataRadioBearerMap/*/"+(pdcp?"NrPdcp":"NrRlc"));
        for (uint32_t i=0;i<matches.GetN();++i) {
            auto object=matches.Get(i);
            if (!m_connected.insert(object).second) continue;
            object->TraceConnectWithoutContext("TxPduPacket",MakeCallback(&RadioTelemetry::Pdu,this).Bind(pdcp,!ue,true));
            object->TraceConnectWithoutContext("RxPduPacket",MakeCallback(&RadioTelemetry::Pdu,this).Bind(pdcp,ue,false));
            if (pdcp) object->TraceConnectWithoutContext("RxPDU",MakeCallback(&RadioTelemetry::PdcpRx,this).Bind(ue));
            else {
                const auto path=matches.GetMatchedPath(i);
                const auto bearerPath=path.substr(0,path.rfind("/NrRlc"));
                auto bearer=DynamicCast<NrDataRadioBearerInfo>(Config::LookupMatches(bearerPath).Get(0));
                const auto rrcPath=path.substr(0,path.find("/DataRadioBearerMap/"));
                auto rrc=Config::LookupMatches(rrcPath).Get(0);
                const uint16_t rnti=ue ? DynamicCast<NrUeRrc>(rrc)->GetRnti()
                                       : DynamicCast<NrUeManager>(rrc)->GetRnti();
                NS_ABORT_MSG_IF(!object->TraceConnectWithoutContext("TxDrop",
                    MakeCallback(&RadioTelemetry::RlcDrop,this).Bind(!ue,rnti,bearer->m_logicalChannelIdentity)),
                    "RLC queue drop trace is unavailable");
            }
        }
    }
}
void RadioTelemetry::Rrc(std::string event,uint64_t imsi,uint16_t cell,uint16_t rnti) {
    if (rnti) {
        const auto index=m_imsiUe.at(imsi);
        auto [found, inserted]=m_rntiUe.emplace(rnti,index);
        NS_ABORT_MSG_IF(!inserted && found->second!=index,"RNTI reused across UEs within telemetry run");
    }
    m_events<<Simulator::Now().GetSeconds()<<','<<event<<",,"<<rnti<<",,,,,imsi="<<imsi<<";cell="<<cell<<'\n';
    Simulator::ScheduleNow(&RadioTelemetry::Discover,this);
}
void RadioTelemetry::ConnectedRa(uint16_t temporaryRnti,uint16_t establishedRnti) {
    m_events<<Simulator::Now().GetSeconds()<<",CRNTI_RECOVERY,UL,"<<establishedRnti
            <<",58,,,,temporary_rnti="<<temporaryRnti<<'\n';
}
void RadioTelemetry::ConnectedRaMsg3(uint64_t imsi,uint16_t temporaryRnti,uint16_t establishedRnti) {
    const auto index=m_imsiUe.at(imsi);
    auto [found,inserted]=m_rntiUe.emplace(temporaryRnti,index);
    NS_ABORT_MSG_IF(!inserted && found->second!=index,"temporary RNTI reused across UEs");
    m_events<<Simulator::Now().GetSeconds()<<",CRNTI_MSG3,UL,"<<establishedRnti
            <<",58,,,,imsi="<<imsi<<";temporary_rnti="<<temporaryRnti<<'\n';
}
void RadioTelemetry::Control(std::string event,uint64_t imsi,SfnSf,uint16_t cell,uint16_t rnti,uint8_t bwp,Ptr<const NrControlMessage> msg) {
    m_events<<Simulator::Now().GetSeconds()<<','<<event<<",,"<<rnti<<",,,,"<<msg->GetMessageType()<<",imsi="<<imsi<<";cell="<<cell<<";bwp="<<+bwp;
    if (msg->GetMessageType()==NrControlMessage::BSR) {
        auto bsr=DynamicCast<NrBsrMessage>(ConstCast<NrControlMessage>(msg))->GetBsr();
        m_events<<";bsr_levels=";
        for (auto level:bsr.m_macCeValue.m_bufferStatus) m_events<<+level<<':';
    }
    m_events<<'\n';
}
void RadioTelemetry::Tx(bool dl,uint32_t rbPerRbg,uint32_t rbCount,Ptr<const PacketBurst> burst,std::shared_ptr<DciInfoElementTdma> dci) {
    uint32_t prbs=0;
    for(uint32_t i=0;i<dci->m_rbgBitmask.size();++i)
        if(dci->m_rbgBitmask[i]) prbs+=std::min(rbPerRbg,rbCount-std::min(rbCount,i*rbPerRbg));
    auto& c=m_counts[dci->m_rnti];
    (dl?c.prbDl:c.prbUl)+=prbs; (dl?c.symDl:c.symUl)+=prbs*dci->m_numSym;
    m_tb<<Simulator::Now().GetSeconds()<<",TX,"<<(dl?"DL":"UL")<<','<<dci->m_rnti<<','<<dci->m_tbSize<<','<<(burst?burst->GetSize():0)<<','<<+dci->m_mcs<<','<<+dci->m_rv<<','<<+dci->m_ndi<<','<<+dci->m_harqProcess<<','<<prbs<<','<<+dci->m_numSym<<",nan,nan,nan\n";
    if (burst && m_payload.is_open()) for(auto packet:burst->GetPackets()) Payload("TX_MAC_PDU",dl,dci->m_rnti,packet);
}
void RadioTelemetry::Rx(bool dl,RxPacketTraceParams p) {
    auto key=std::make_tuple(dl,p.m_rnti,p.m_frameNum,p.m_subframeNum,p.m_slotNum,p.m_symStart);
    if (!m_seen.insert(key).second) return;
    m_tb<<Simulator::Now().GetSeconds()<<",RX,"<<(dl?"DL":"UL")<<','<<p.m_rnti<<','<<p.m_tbSize<<",nan,"<<+p.m_mcs<<','<<+p.m_rv<<",nan,nan,"<<p.m_rbAssignedNum<<','<<+p.m_numSym<<','<<10*std::log10(p.m_sinr)<<','<<p.m_tbler<<','<<!p.m_corrupt<<'\n';
}
void RadioTelemetry::MacRx(bool dl,Ptr<const Packet> packet,uint16_t rnti,bool corrupt) {
    if (!corrupt && m_payload.is_open()) Payload("RX_MAC_PDU_MODEL_OK",dl,rnti,packet);
}
void RadioTelemetry::Boundary(bool dl,Time overlap) {
    m_events<<Simulator::Now().GetSeconds()<<",NTN_BOUNDARY_NS,"<<(dl?"DL":"UL")<<",,,,,"<<overlap.GetNanoSeconds()<<",\n";
}
void RadioTelemetry::Harq(bool dl,uint16_t rnti,uint8_t process,bool ack) {
    m_events<<Simulator::Now().GetSeconds()<<",HARQ_GENERATED,"<<(dl?"DL":"UL")<<','<<rnti<<",,,,"<<ack<<",process="<<+process<<'\n';
}
void RadioTelemetry::Pdu(bool pdcp,bool dl,bool tx,uint16_t rnti,uint8_t lcid,Ptr<const Packet> packet) {
    int sn=-1; uint32_t headerBytes=0;
    if (pdcp) {NrPdcpHeader h;headerBytes=packet->PeekHeader(h);sn=h.GetSequenceNumber();}
    else {NrRlcAmHeader h;packet->PeekHeader(h);if(h.IsDataPdu()) sn=h.GetSequenceNumber().GetValue();}
    m_events<<Simulator::Now().GetSeconds()<<','<<(pdcp?"PDCP_":"RLC_AM_")<<(tx?"TX":"RX")<<','<<(dl?"DL":"UL")<<','<<rnti<<','<<+lcid<<','<<sn<<','<<packet->GetSize()<<",,\n";
    if (pdcp && lcid>2) {
        auto& c=m_counts[rnti]; const auto n=packet->GetSize()-headerBytes;
        if(tx) (dl?c.txDl:c.txUl)+=n; else (dl?c.rxDl:c.rxUl)+=n;
    }
}
void RadioTelemetry::PdcpRx(bool dl,uint16_t rnti,uint8_t lcid,uint32_t,uint64_t delayNs) {
    if(dl && lcid>2) {auto& c=m_counts[rnti];c.delayUs+=delayNs/1000.0;++c.delayN;}
}
void RadioTelemetry::RlcDrop(bool dl,uint16_t rnti,uint8_t lcid,Ptr<const Packet> packet) {
    m_events<<Simulator::Now().GetSeconds()<<",RLC_TX_DROP,"<<(dl?"DL":"UL")<<','
            <<rnti<<','<<+lcid<<",,"<<packet->GetSize()<<",,reason=tx_buffer_full\n";
    auto& c=m_counts[rnti];
    ++(dl?c.dropsDl:c.dropsUl);
    (dl?c.dropBytesDl:c.dropBytesUl)+=packet->GetSize();
}
void RadioTelemetry::Payload(const char* event,bool dl,uint16_t rnti,Ptr<const Packet> packet) {
    std::vector<uint8_t> data(packet->GetSize());packet->CopyData(data.data(),data.size());
    m_payload<<Simulator::Now().GetSeconds()<<','<<event<<','<<(dl?"DL":"UL")<<','<<rnti<<','<<packet->GetUid()<<','<<data.size()<<',';
    for (auto byte:data) m_payload<<std::hex<<std::setw(2)<<std::setfill('0')<<+byte;
    m_payload<<std::dec<<'\n';
}
void RadioTelemetry::Tick() {
    const auto now=Simulator::Now();const double dt=(now-m_previous).GetSeconds();
    if(dt<=0) return;
    for(uint32_t i=0;i<m_ues.GetN();++i) {
        auto dev=DynamicCast<NrUeNetDevice>(m_ues.Get(i)); const uint16_t rnti=dev->GetRrc()->GetRnti();
        if (rnti) m_rntiUe.emplace(rnti,i);
        Counters c;
        std::string history;
        // A reconnect may change RNTI between ticks. Attribute both the old
        // and new identities to the stable UE instead of discarding old work.
        for (const auto& [identity,index]:m_rntiUe) if(index==i) {
            if(!history.empty()) history+='|';
            history+=std::to_string(identity);
            const auto found=m_counts.find(identity);
            if(found==m_counts.end()) continue;
            const auto& x=found->second;
            c.txDl+=x.txDl;c.txUl+=x.txUl;c.rxDl+=x.rxDl;c.rxUl+=x.rxUl;
            c.prbDl+=x.prbDl;c.prbUl+=x.prbUl;c.symDl+=x.symDl;c.symUl+=x.symUl;
            c.delayUs+=x.delayUs;c.delayN+=x.delayN;
            c.dropsDl+=x.dropsDl;c.dropsUl+=x.dropsUl;
            c.dropBytesDl+=x.dropBytesDl;c.dropBytesUl+=x.dropBytesUl;
        }
        m_kpi<<now.GetSeconds()<<','<<dt<<','<<i<<','<<rnti<<','<<c.txDl/1000<<','<<c.txUl/1000<<','<<(c.delayN?c.delayUs/c.delayN:std::numeric_limits<double>::quiet_NaN())<<','<<c.prbDl<<','<<c.prbUl<<','<<c.rxDl*8/dt/1e6<<','<<c.rxUl*8/dt/1e6<<','<<c.symDl<<','<<c.symUl<<','<<c.delayN<<','<<c.dropsDl<<','<<c.dropsUl<<','<<c.dropBytesDl<<','<<c.dropBytesUl<<','<<history<<'\n';
    }
    for (const auto& [identity,c]:m_counts)
        NS_ABORT_MSG_IF(identity && !m_rntiUe.count(identity),"unmapped RNTI in radio telemetry");
    m_counts.clear();m_previous=now;
    if(now+m_period<m_stop) m_tick=Simulator::Schedule(m_period,&RadioTelemetry::Tick,this);
}
void RadioTelemetry::Finish() {
    Simulator::Cancel(m_tick);Tick();Simulator::Cancel(m_tick);
    for(auto* f:{&m_events,&m_tb,&m_kpi,&m_payload}) f->flush();
}
}
