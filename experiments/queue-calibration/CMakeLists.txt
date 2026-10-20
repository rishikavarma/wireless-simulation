# Calibration observer links the same scenario implementation and model.
set(scenario_root "${CMAKE_SOURCE_DIR}/scratch/sat-bs-handset")
set(calibration_sources queue-calibration.cc
  "${scenario_root}/ue/ue-tcp-client.cc"
  "${scenario_root}/bs/bs-echo-server.cc"
  "${scenario_root}/common/json.cc"
  "${scenario_root}/common/tcp-pipe.cc"
  "${scenario_root}/common/sim-config.cc"
  "${scenario_root}/common/app-config.cc"
  "${scenario_root}/common/ntn-helpers.cc"
  "${scenario_root}/metrics/radio-telemetry.cc"
  "${scenario_root}/metrics/direct-traffic.cc"
  "${scenario_root}/metrics/phy-metrics.cc"
  "${scenario_root}/metrics/ue-metrics.cc"
  "${scenario_root}/metrics/bs-metrics.cc"
  "${scenario_root}/metrics/metrics-report.cc"
  "${scenario_root}/topology/mobility.cc"
  "${scenario_root}/topology/relay-handover.cc"
  "${scenario_root}/topology/bent-pipe-channel.cc"
  "${scenario_root}/topology/nr-ntn-feeder.cc"
)
get_scratch_prefix(calibration_prefix)
build_exec(
  EXECNAME queue-calibration
  EXECNAME_PREFIX ${calibration_prefix}
  SOURCE_FILES ${calibration_sources}
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/queue-calibration
)
target_include_directories(${calibration_prefix}queue-calibration PRIVATE ${scenario_root})
