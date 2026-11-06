// Reuse the maintained scenario with an additional, read-only TCP RTT observer.
#define main SatelliteExperimentMain
#include "sat-bs-handset.cc"
#undef main

#include <vector>
#include "ns3/node-list.h"
#include "ns3/uinteger.h"

namespace
{
/** Record actual socket RTT samples, retaining socket identity and excluding warm-up. */
void
RecordLastRtt(std::ostream* output, std::string context, Time previous, Time current)
{
    if (current.IsStrictlyPositive())
    {
        *output << Simulator::Now().GetSeconds() << ',' << context << ','
                << current.GetSeconds() * 1000.0 << '\n';
    }
}

/** Attach after connection setup; do not include the SYN RTT in the reference. */
void
AttachRttObserver(std::ostream* output)
{
    const bool attached = Config::ConnectFailSafe(
        "/NodeList/*/$ns3::TcpL4Protocol/SocketList/*/LastRTT",
        MakeBoundCallback(&RecordLastRtt, output));
    *output << "# observer_attached=" << attached << '\n';
    const auto sockets = Config::LookupMatches("/NodeList/*/$ns3::TcpL4Protocol/SocketList/*");
    for (uint32_t i = 0; i < sockets.GetN(); ++i)
    {
        UintegerValue count;
        if (sockets.Get(i)->GetAttributeFailSafe("DelAckCount", count))
        {
            TimeValue timeout;
            sockets.Get(i)->GetAttribute("DelAckTimeout", timeout);
            *output << "# ack_policy socket=" << sockets.GetMatchedPath(i)
                    << " del_ack_count=" << count.Get()
                    << " del_ack_timeout_ms=" << timeout.Get().GetMilliSeconds() << '\n';
        }
    }
}

/** Timestamp the real SR/BSR/grant path without changing scheduling. */
void
RecordControlConst(std::ostream* output, const char* stage, SfnSf slot, uint16_t,
                   uint16_t rnti, uint8_t bwp, Ptr<const NrControlMessage> message)
{
    const auto type = message->GetMessageType();
    const char* kind = nullptr;
    int k2 = -1;
    if (type == NrControlMessage::SR) kind = "SR";
    else if (type == NrControlMessage::BSR) kind = "BSR";
    else if (type == NrControlMessage::UL_DCI)
    {
        kind = "UL_DCI";
        auto grant = StaticCast<const NrUlDciMessage>(message);
        rnti = ConstCast<NrUlDciMessage>(grant)->GetDciInfoElement()->m_rnti;
        k2 = static_cast<int>(grant->GetKDelay());
    }
    if (!kind) return;
    *output << Simulator::Now().GetNanoSeconds() << ',' << stage << ',' << kind << ','
            << rnti << ',' << +bwp << ',' << slot.Normalize() << ',' << k2 << '\n';
}

void
RecordSr(std::ostream* output, uint8_t bwp, uint16_t rnti)
{
    *output << Simulator::Now().GetNanoSeconds() << ",gnb_mac,SR," << rnti << ','
            << +bwp << ",-1,-1\n";
}

void
RecordUlData(std::ostream* output, Ptr<NrUePhy> phy, Ptr<const PacketBurst>,
             std::shared_ptr<DciInfoElementTdma> dci)
{
    *output << Simulator::Now().GetNanoSeconds() << ",ue_phy_tx,UL_DATA,"
            << phy->GetRnti() << ",1," << phy->GetCurrentSfnSf().Normalize()
            << ",-1\n";
}

void
AttachTimingObserver(std::ostream* output)
{
    for (auto node = NodeList::Begin(); node != NodeList::End(); ++node)
    {
        for (uint32_t i = 0; i < (*node)->GetNDevices(); ++i)
        {
            auto device = (*node)->GetDevice(i);
            if (DynamicCast<NrUeNetDevice>(device))
            {
                auto dl = NrHelper::GetUePhy(device, 0);
                auto ul = NrHelper::GetUePhy(device, 1);
                dl->TraceConnectWithoutContext("UePhyRxedCtrlMsgsTrace",
                    MakeBoundCallback(&RecordControlConst, output, "ue_phy_rx"));
                ul->TraceConnectWithoutContext("UePhyRxedCtrlMsgsTrace",
                    MakeBoundCallback(&RecordControlConst, output, "ue_phy_rx"));
                ul->TraceConnectWithoutContext("UePhyTxedCtrlMsgsTrace",
                    MakeBoundCallback(&RecordControlConst, output, "ue_phy_tx"));
                ul->GetSpectrumPhy()->TraceConnectWithoutContext("TxTransportBlock",
                    MakeBoundCallback(&RecordUlData, output, ul));
            }
            else if (DynamicCast<NrGnbNetDevice>(device))
            {
                auto dl = NrHelper::GetGnbPhy(device, 0);
                auto ul = NrHelper::GetGnbPhy(device, 1);
                dl->TraceConnectWithoutContext("GnbPhyTxedCtrlMsgsTrace",
                    MakeBoundCallback(&RecordControlConst, output, "gnb_phy_tx"));
                ul->TraceConnectWithoutContext("GnbPhyRxedCtrlMsgsTrace",
                    MakeBoundCallback(&RecordControlConst, output, "gnb_phy_rx"));
                NrHelper::GetGnbMac(device, 1)->TraceConnectWithoutContext("SrReq",
                    MakeBoundCallback(&RecordSr, output));
            }
        }
    }
}
} // namespace

/** Forward scenario arguments and schedule the additional calibration observer. */
int
main(int argc, char* argv[])
{
    std::string path;
    std::string timingPath;
    std::vector<char*> forwarded;
    for (int i = 0; i < argc; ++i)
    {
        const std::string argument(argv[i]);
        const std::string prefix = "--calibrationRttCsv=";
        const std::string timingPrefix = "--calibrationTimingCsv=";
        if (argument.rfind(prefix, 0) == 0)
        {
            path = argument.substr(prefix.size());
        }
        else if (argument.rfind(timingPrefix, 0) == 0)
        {
            timingPath = argument.substr(timingPrefix.size());
        }
        else
        {
            forwarded.push_back(argv[i]);
        }
    }
    std::ofstream output;
    std::ofstream timingOutput;
    if (!path.empty())
    {
        output.open(path);
        NS_ABORT_MSG_IF(!output, "cannot open calibration RTT output " << path);
        output << "time_s,socket,rtt_ms\n";
        Simulator::Schedule(Seconds(20), &AttachRttObserver, &output);
    }
    if (!timingPath.empty())
    {
        timingOutput.open(timingPath);
        NS_ABORT_MSG_IF(!timingOutput, "cannot open calibration timing output " << timingPath);
        timingOutput << "time_ns,stage,event,rnti,bwp,slot,k2_slots\n";
        Simulator::Schedule(MilliSeconds(1), &AttachTimingObserver, &timingOutput);
    }
    return SatelliteExperimentMain(static_cast<int>(forwarded.size()), forwarded.data());
}
