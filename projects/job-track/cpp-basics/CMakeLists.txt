cmake_minimum_required(VERSION 3.16)
project(job_track_cpp_basics LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(ENABLE_WARNINGS "Enable strict compiler warnings" ON)

add_library(vehicle_state src/vehicle_state.cpp)
target_include_directories(vehicle_state PUBLIC include)

if(ENABLE_WARNINGS)
  target_compile_options(vehicle_state PRIVATE
    -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
endif()

add_executable(vehicle_state_demo src/main.cpp)
target_link_libraries(vehicle_state_demo PRIVATE vehicle_state)

enable_testing()
add_executable(vehicle_state_test tests/state_test.cpp)
target_link_libraries(vehicle_state_test PRIVATE vehicle_state)
add_test(NAME vehicle_state_test COMMAND vehicle_state_test)
