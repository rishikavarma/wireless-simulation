/*
 * Sample count, minimum, maximum, and mean for one metric series.
 */

#ifndef SAT_BS_HANDSET_COMMON_SERIES_STATS_H
#define SAT_BS_HANDSET_COMMON_SERIES_STATS_H

#include <algorithm>
#include <cstdint>
#include <vector>

namespace ns3
{

struct SeriesStats
{
    uint64_t samples{0};
    double min{0.0};
    double max{0.0};
    double mean{0.0};
};

inline SeriesStats
SummarizeSeries(const std::vector<double>& samples)
{
    SeriesStats s;
    s.samples = samples.size();
    if (samples.empty())
    {
        return s;
    }
    double sum = 0.0;
    s.min = samples.front();
    s.max = samples.front();
    for (double v : samples)
    {
        sum += v;
        s.min = std::min(s.min, v);
        s.max = std::max(s.max, v);
    }
    s.mean = sum / static_cast<double>(samples.size());
    return s;
}

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_SERIES_STATS_H */
