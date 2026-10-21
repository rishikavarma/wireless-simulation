# Calibration observer links the same scenario implementation and model.
set(scenario_root "${CMAKE_SOURCE_DIR}/scratch/sat-bs-handset")
set(regression_sources relay-switch-regression.cc
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
get_scratch_prefix(regression_prefix)
build_exec(
  EXECNAME relay-switch-regression
  EXECNAME_PREFIX ${regression_prefix}
  SOURCE_FILES ${regression_sources}
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/relay-switch-regression
)
target_include_directories(${regression_prefix}relay-switch-regression PRIVATE ${scenario_root})
