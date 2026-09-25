# Multi-file sat-bs-handset scenario.
# Layout is role-based so more UEs / base stations can land in ue/ and bs/
# without flattening the tree. Nested .cc files require this CMakeLists
# (ns-3's default scratch GLOB only sees top-level sources).

set(sat_bs_handset_sources
  sat-bs-handset.cc
  ue/ue-offload-client.cc
  bs/bs-offload-gateway.cc
  satellite/sat-compute-server.cc
  common/ntn-helpers.cc
  topology/mobility.cc
  topology/nr-ntn-feeder.cc
  topology/wifi-access.cc
)

get_scratch_prefix(sat_bs_handset_prefix)

build_exec(
  EXECNAME sat-bs-handset
  EXECNAME_PREFIX ${sat_bs_handset_prefix}
  SOURCE_FILES ${sat_bs_handset_sources}
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/sat-bs-handset
)

# Includes use paths from the scenario root, e.g. "ue/ue-offload-client.h".
target_include_directories(
  ${sat_bs_handset_prefix}sat-bs-handset
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
)
