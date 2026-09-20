cmake_minimum_required(VERSION 3.22)
project(CarlaUfbxProbe VERSION 0.23.0 LANGUAGES C CXX)

if(NOT CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
  message(FATAL_ERROR "This substage requires a native ARM64 host")
endif()
if(NOT EXISTS "${CARLA_UFBX_SOURCE_DIR}/ufbx.c" OR NOT EXISTS "${CARLA_UFBX_SOURCE_DIR}/test/unit_tests.c")
  message(FATAL_ERROR "CARLA_UFBX_SOURCE_DIR must contain the pinned source and test directory")
endif()
set(rapidjson "$ENV{CARLA_UE_DIR}/Engine/Source/ThirdParty/RapidJSON/1.1.0")
if(NOT EXISTS "${rapidjson}/rapidjson/writer.h")
  message(FATAL_ERROR "The existing UE RapidJSON headers are required")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)
set(CMAKE_INSTALL_RPATH "$ORIGIN;$ORIGIN/../lib")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")

add_library(ufbx_objects OBJECT "${CARLA_UFBX_SOURCE_DIR}/ufbx.c")
set_target_properties(ufbx_objects PROPERTIES POSITION_INDEPENDENT_CODE ON)
add_library(ufbx SHARED $<TARGET_OBJECTS:ufbx_objects>)
target_include_directories(ufbx PUBLIC "${CARLA_UFBX_SOURCE_DIR}")
target_link_libraries(ufbx PRIVATE m)
set_target_properties(ufbx PROPERTIES VERSION "${PROJECT_VERSION}" SOVERSION "0.23")
add_library(ufbx_static STATIC $<TARGET_OBJECTS:ufbx_objects>)
target_include_directories(ufbx_static PUBLIC "${CARLA_UFBX_SOURCE_DIR}")
target_link_libraries(ufbx_static PUBLIC m)
set_target_properties(ufbx_static PROPERTIES OUTPUT_NAME ufbx
  ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")

add_executable(ufbx-probe ufbx-probe.cpp)
target_include_directories(ufbx-probe SYSTEM PRIVATE "${rapidjson}")
target_compile_options(ufbx-probe PRIVATE -Wall -Wextra -Werror -Wno-unused-command-line-argument)
target_link_libraries(ufbx-probe PRIVATE ufbx)

# This upstream executable tests parser arithmetic/sorting, not the complete FBX suite.
add_executable(ufbx-unit-tests "${CARLA_UFBX_SOURCE_DIR}/test/unit_tests.c")
target_link_libraries(ufbx-unit-tests PRIVATE m)

install(TARGETS ufbx ufbx_static ufbx-probe ufbx-unit-tests
  LIBRARY DESTINATION lib ARCHIVE DESTINATION lib RUNTIME DESTINATION bin)
install(FILES "${CARLA_UFBX_SOURCE_DIR}/ufbx.h" DESTINATION include)
install(FILES "${CARLA_UFBX_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/ufbx)
