#include "common/app-config.h"

#include "ns3/abort.h"

namespace ns3
{

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
    NS_ABORT_MSG("Unknown application.probe.traffic '" << name
                                                       << "' (try cbr, onoff, or bursty)");
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

} // namespace ns3
