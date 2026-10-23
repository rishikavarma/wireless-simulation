set(scenario_root "${CMAKE_SOURCE_DIR}/scratch/sat-bs-handset")
get_scratch_prefix(regression_prefix)
build_exec(
  EXECNAME echo-drain-regression
  EXECNAME_PREFIX ${regression_prefix}
  SOURCE_FILES echo-drain-regression.cc
    "${scenario_root}/ue/ue-tcp-client.cc"
    "${scenario_root}/bs/bs-echo-server.cc"
    "${scenario_root}/common/tcp-pipe.cc"
    "${scenario_root}/common/app-config.cc"
    "${scenario_root}/metrics/ue-metrics.cc"
    "${scenario_root}/metrics/bs-metrics.cc"
    "${scenario_root}/metrics/phy-metrics.cc"
    "${scenario_root}/topology/bent-pipe-channel.cc"
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/echo-drain-regression
)
target_include_directories(${regression_prefix}echo-drain-regression PRIVATE ${scenario_root})
