// Exercise the production scenario and observe actual uplink transmissions.
#define main SatelliteExperimentMain
#include "sat-bs-handset.cc"
#undef main

#include "ns3/node-list.h"

namespace
{
void RecordPhase(std::ostream* output, Ptr<NrUePhy> phy, const SfnSf& slot,
                 uint8_t symbol, const char* kind)
{
    const Time expected = phy->GetSlotPeriod() * slot.Normalize() +
                          phy->GetSymbolPeriod() * symbol;
    const Time arrival = Simulator::Now() + phy->GetNtnOneWayDelay();
    *output << Simulator::Now().GetNanoSeconds() << ',' << phy->GetRnti() << ','
            << kind << ',' << slot.Normalize() << ',' << +symbol << ','
            << (arrival - expected).GetNanoSeconds() << '\n';
}

void Control(std::ostream* output, Ptr<NrUePhy> phy, SfnSf slot, uint16_t,
             uint16_t, uint8_t, Ptr<const NrControlMessage> message)
{
    if (message->GetMessageType() != NrControlMessage::SRS)
    {
        // These fixtures retain the scenario's one-symbol UL control region.
        RecordPhase(output, phy, slot, phy->GetSymbolsPerSlot() - 1, "CTRL");
    }
}

void Data(std::ostream* output, Ptr<NrUePhy> phy, Ptr<const PacketBurst>,
          std::shared_ptr<DciInfoElementTdma> dci)
{
    RecordPhase(output, phy, phy->GetCurrentSfnSf(), dci->m_symStart, "DATA");
}

void Attach(std::ostream* output)
{
    for (auto it = NodeList::Begin(); it != NodeList::End(); ++it)
    {
        auto node = *it;
        for (uint32_t i = 0; i < node->GetNDevices(); ++i)
        {
            auto device = DynamicCast<NrUeNetDevice>(node->GetDevice(i));
            if (!device) continue;
            auto phy = NrHelper::GetUePhy(device, 1);
            phy->TraceConnectWithoutContext("UePhyTxedCtrlMsgsTrace",
                MakeBoundCallback(&Control, output, phy));
            phy->GetSpectrumPhy()->TraceConnectWithoutContext("TxTransportBlock",
                MakeBoundCallback(&Data, output, phy));
        }
    }
}
}

int main(int argc, char* argv[])
{
    std::string path;
    std::vector<char*> forwarded;
    for (int i = 0; i < argc; ++i)
    {
        const std::string argument(argv[i]);
        const std::string prefix = "--phaseCsv=";
        if (argument.rfind(prefix, 0) == 0) path = argument.substr(prefix.size());
        else forwarded.push_back(argv[i]);
    }
    std::ofstream output(path);
    NS_ABORT_MSG_IF(!output, "--phaseCsv must name a writable output file");
    output << "tx_time_ns,rnti,kind,ground_slot,symbol,arrival_error_ns\n";
    Simulator::Schedule(Seconds(.001), &Attach, &output);
    return SatelliteExperimentMain(static_cast<int>(forwarded.size()), forwarded.data());
}
