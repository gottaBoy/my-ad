cmake_minimum_required(VERSION 3.22)
project(CarlaAssimpProbe LANGUAGES C CXX)

if(NOT EXISTS "${CARLA_ASSIMP_SOURCE_DIR}/CMakeLists.txt")
  message(FATAL_ERROR "CARLA_ASSIMP_SOURCE_DIR must point to the pinned Assimp source")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)
set(CMAKE_INSTALL_RPATH "$ORIGIN;$ORIGIN/../lib")
# Explicit UE libc++ headers make the driver-only stdlib flag redundant at compile time.
add_compile_options("$<$<COMPILE_LANGUAGE:CXX>:-Wno-unused-command-line-argument>")
add_subdirectory("${CARLA_ASSIMP_SOURCE_DIR}" assimp)
# Bind internal STL functions and vtables together across the UE allocator boundary.
target_link_options(assimp PRIVATE "-Wl,-Bsymbolic")

add_executable(fbx-probe fbx-probe.cpp)
target_link_libraries(fbx-probe PRIVATE assimp::assimp)
target_include_directories(fbx-probe SYSTEM PRIVATE "${CARLA_ASSIMP_SOURCE_DIR}/contrib/rapidjson/include")
target_compile_options(fbx-probe PRIVATE -Wall -Wextra -Werror)
install(TARGETS fbx-probe RUNTIME DESTINATION bin)

# Compile the unmodified upstream FBX suite, without unrelated importer suites.
set(gtest "${CARLA_ASSIMP_SOURCE_DIR}/contrib/googletest/googletest")
add_executable(assimp-fbx-tests
  "${CARLA_ASSIMP_SOURCE_DIR}/test/unit/utFBXImporterExporter.cpp"
  "${CARLA_ASSIMP_SOURCE_DIR}/test/unit/AbstractImportExportBase.cpp"
  "${gtest}/src/gtest-all.cc"
  "${gtest}/src/gtest_main.cc")
target_include_directories(assimp-fbx-tests PRIVATE
  "${CARLA_ASSIMP_SOURCE_DIR}/test/unit" "${gtest}/include" "${gtest}")
target_compile_definitions(assimp-fbx-tests PRIVATE
  ASSIMP_TEST_MODELS_DIR="${CARLA_ASSIMP_SOURCE_DIR}/test/models")
target_link_libraries(assimp-fbx-tests PRIVATE assimp::assimp pthread)
