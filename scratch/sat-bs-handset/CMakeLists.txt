# Multi-file sat-bs-handset scenario (transparent NTN bent-pipe).
# Nested .cc files require this CMakeLists (ns-3's default scratch GLOB only
# sees top-level sources).

set(sat_bs_handset_sources
  sat-bs-handset.cc
  ue/ue-tcp-client.cc
  bs/bs-echo-server.cc
  common/json.cc
  common/tcp-pipe.cc
  common/sim-config.cc
  common/app-config.cc
  common/ntn-helpers.cc
  metrics/phy-metrics.cc
  metrics/ue-metrics.cc
  metrics/bs-metrics.cc
  metrics/metrics-report.cc
  topology/mobility.cc
  topology/bent-pipe-channel.cc
  topology/nr-ntn-feeder.cc
)

get_scratch_prefix(sat_bs_handset_prefix)

build_exec(
  EXECNAME sat-bs-handset
  EXECNAME_PREFIX ${sat_bs_handset_prefix}
  SOURCE_FILES ${sat_bs_handset_sources}
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/sat-bs-handset
)

# Includes use paths from the scenario root, e.g. "ue/ue-tcp-client.h".
target_include_directories(
  ${sat_bs_handset_prefix}sat-bs-handset
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
)
