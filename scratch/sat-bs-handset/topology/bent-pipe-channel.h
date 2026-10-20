/*
 * Transparent bent-pipe between a ground gNB and a ground UE.
 * The satellite is a transparent relay. Path loss is the feeder hop plus
 * the service hop, with independent transmit budgets and composite hop SNR. The one-way light time
 * is the NR channel delay. The handset slot clock starts that much later,
 * so a downlink burst arrives as the handset's slot begins.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H
#define SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H

#include "ns3/phased-array-spectrum-propagation-loss-model.h"
#include "ns3/propagation-delay-model.h"
#include "ns3/propagation-loss-model.h"
#include "ns3/spectrum-transmit-filter.h"
#include <map>

namespace ns3
{

class MobilityModel;
class ThreeGppPropagationLossModel;

class BentPipePropagationLossModel : public PropagationLossModel
{
  public:
    static TypeId GetTypeId();

    /** Visibility gate. Visible paths have unity scalar gain; spectrum model owns the budget. */
    void Configure(Ptr<MobilityModel> gnb,
                   Ptr<MobilityModel> ue,
                   Ptr<MobilityModel> satellite,
                   double feederMinElevationDeg,
                   double serviceMinElevationDeg);

    /** Register a UE-specific transparent relay path. */
    void RegisterPath(Ptr<const MobilityModel> ue, Ptr<BentPipePropagationLossModel> path);
    /** Resolve either channel endpoint to its own path, or this primary path. */
    const BentPipePropagationLossModel* Resolve(Ptr<const MobilityModel> a, Ptr<const MobilityModel> b) const;
    /** Return the relay used by this path. */
    Ptr<MobilityModel> GetSatellite() const { return m_satellite; }
    static double ElevationDeg(Ptr<const MobilityModel> ground,
                               Ptr<const MobilityModel> satellite);
    double GetFeederElevationDeg() const;
    double GetServiceElevationDeg() const;
    bool IsVisible() const;
    /** Set the shared relay interruption gate; visibility still additionally requires both hop
     * elevation masks.
     */
    void SetAvailable(bool available) { m_available = available; }

  private:
    double DoCalcRxPower(double txPowerDbm,
                         Ptr<MobilityModel> a,
                         Ptr<MobilityModel> b) const override;
    int64_t DoAssignStreams(int64_t stream) override;

    std::map<Ptr<const MobilityModel>, Ptr<BentPipePropagationLossModel>> m_paths; ///< Per-UE visibility and relay.
    Ptr<MobilityModel> m_gnb;
    Ptr<MobilityModel> m_ue;
    Ptr<MobilityModel> m_satellite;
    double m_feederMinElevationDeg{30.0};
    double m_serviceMinElevationDeg{30.0};
    bool m_available{true};
};

/** Discard hidden paths at TX; the scalar gate also checks again at RX. */
class BentPipeVisibilityFilter : public SpectrumTransmitFilter
{
  public:
    static TypeId GetTypeId();
    void Configure(Ptr<BentPipePropagationLossModel> visibility);
    uint64_t GetBlockedTransmissions() const;

  private:
    bool DoFilter(Ptr<const SpectrumSignalParameters> params,
                  Ptr<const SpectrumPhy> receiverPhy) override;
    int64_t DoAssignStreams(int64_t stream) override;
    Ptr<BentPipePropagationLossModel> m_visibility;
    mutable uint64_t m_blocked{0};
};

class BentPipeDelayModel : public PropagationDelayModel
{
  public:
    static TypeId GetTypeId();

    /**
     * Configure a transparent bent-pipe path. The delay is recomputed from
     * the current positions on every transmission.
     */
    void Configure(Ptr<MobilityModel> gnb,
                   Ptr<MobilityModel> ue,
                   Ptr<MobilityModel> satellite);

    /** Register an independently timed background path. */
    void RegisterPath(Ptr<const MobilityModel> ue, Ptr<BentPipeDelayModel> path) { m_paths[ue] = path; }
    Time GetCurrentDelay() const;
    Time GetFeederDelay() const;
    Time GetServiceDelay() const;
    Time GetCommonTimingAdvance() const;
    Time GetUeSpecificTimingAdvance() const;
    Time GetTotalTimingAdvance() const;

    Time GetDelay(Ptr<MobilityModel> a, Ptr<MobilityModel> b) const override;

  private:
    int64_t DoAssignStreams(int64_t stream) override;

    std::map<Ptr<const MobilityModel>, Ptr<BentPipeDelayModel>> m_paths; ///< Per-UE delay models.
    Ptr<MobilityModel> m_gnb;
    Ptr<MobilityModel> m_ue;
    Ptr<MobilityModel> m_satellite;
};

/**
 * Noise-limited SNS3-style two-hop abstraction, adapted to NR's receiver PSD.
 * Independent satellite EIRP and actual source PSD; no per-run gain calibration.
 * Composite SNR = 1/(1/SNR_first + 1/SNR_second). One UE/cell only; this
 * equivalent-PSD mapping does not model two-hop co-channel interference.
 */
class BentPipeSpectrumModel : public PhasedArraySpectrumPropagationLossModel
{
  public:
    static TypeId GetTypeId();
    void Configure(Ptr<BentPipePropagationLossModel> visibility,
                   Ptr<MobilityModel> satellite,
                   Ptr<ThreeGppPropagationLossModel> firstHopLoss,
                   Ptr<ThreeGppPropagationLossModel> secondHopLoss,
                   double sourceGainDb,
                   double satelliteRxGainDb,
                   double satelliteEirpDensityDbwMhz,
                   double destinationGainDb,
                   double satelliteNoiseFigureDb,
                   double destinationNoiseFigureDb,
                   double firstExcessLossDb = 0.0,
                   double secondExcessLossDb = 0.0);

    /** Equivalent PSD whose division by NR receiver noise gives composite SNR. */
    double EquivalentRxPsd(double sourcePsd,
                           double firstHopLossDb,
                           double secondHopLossDb) const;

  private:
    Ptr<SpectrumSignalParameters> DoCalcRxPowerSpectralDensity(
        Ptr<const SpectrumSignalParameters> params,
        Ptr<const MobilityModel> a,
        Ptr<const MobilityModel> b,
        Ptr<const PhasedArrayModel> aPhasedArrayModel,
        Ptr<const PhasedArrayModel> bPhasedArrayModel) const override;
    int64_t DoAssignStreams(int64_t stream) override;
    Ptr<BentPipePropagationLossModel> m_visibility;
    Ptr<MobilityModel> m_satellite;
    Ptr<ThreeGppPropagationLossModel> m_firstHopLoss;
    Ptr<ThreeGppPropagationLossModel> m_secondHopLoss;
    double m_sourceGainDb{0};
    double m_satelliteRxGainDb{0};
    double m_satelliteEirpDensityDbwMhz{0};
    double m_destinationGainDb{0};
    double m_satelliteNoisePsd{0};
    double m_destinationNoisePsd{0};
    double m_firstExcessLossDb{0};
    double m_secondExcessLossDb{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_BENT_PIPE_CHANNEL_H */
