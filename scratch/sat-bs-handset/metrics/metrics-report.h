/*
 * Writes the post-run report: UE message counts, TCP features, BS counts, SINR.
 */

#ifndef SAT_BS_HANDSET_COMMON_METRICS_REPORT_H
#define SAT_BS_HANDSET_COMMON_METRICS_REPORT_H

#include "metrics/bs-metrics.h"
#include "metrics/phy-metrics.h"
#include "metrics/ue-metrics.h"

#include "ns3/ipv4-address.h"

#include <ostream>

namespace ns3
{

void WriteRunMetrics(std::ostream& os,
                     Ipv4Address ueAddr,
                     Ipv4Address bsAddr,
                     const UeAppMetrics& ue,
                     const BsAppMetrics& bs,
                     const NrPhyMetrics& phy);

/** Workload counts and the success check, for results/<prefix>/<prefix>-app.txt. */
void WriteAppOutcome(std::ostream& os, const UeAppMetrics& ue);

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_METRICS_REPORT_H */
