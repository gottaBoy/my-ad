set(ue_dir "$ENV{CARLA_UE_DIR}")
set(llvm_bin "$ENV{CARLA_LLVM_BIN}")
if(NOT llvm_bin)
  set(llvm_bin "/usr/lib/llvm-18/bin")
endif()

if(NOT CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
  message(FATAL_ERROR "This toolchain requires a native ARM64 host")
endif()

set(triple "aarch64-unknown-linux-gnueabi")
file(GLOB sysroots LIST_DIRECTORIES TRUE
  "${ue_dir}/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/${triple}")
list(LENGTH sysroots sysroot_count)
if(NOT sysroot_count EQUAL 1)
  message(FATAL_ERROR "Expected one ARM64 UE sysroot, found ${sysroot_count}")
endif()
list(GET sysroots 0 CMAKE_SYSROOT)

set(libcxx "${ue_dir}/Engine/Source/ThirdParty/Unix/LibCxx")
set(lib_dir "${libcxx}/lib/Unix/${triple}")
foreach(lib libc++.a libc++abi.a)
  if(NOT EXISTS "${lib_dir}/${lib}")
    message(FATAL_ERROR "Missing UE ARM64 library: ${lib_dir}/${lib}")
  endif()
endforeach()

# These are native host tools; setting CMAKE_SYSTEM_NAME would unnecessarily
# make DXC cross-compile its table generators.
set(CMAKE_C_COMPILER "${llvm_bin}/clang")
set(CMAKE_CXX_COMPILER "${llvm_bin}/clang++")
set(CMAKE_C_COMPILER_TARGET "${triple}")
set(CMAKE_CXX_COMPILER_TARGET "${triple}")
set(CMAKE_AR "${llvm_bin}/llvm-ar" CACHE FILEPATH "")
set(CMAKE_RANLIB "${llvm_bin}/llvm-ranlib" CACHE FILEPATH "")
set(CMAKE_NM "${llvm_bin}/llvm-nm" CACHE FILEPATH "")
set(CMAKE_LINKER "${llvm_bin}/ld.lld" CACHE FILEPATH "")
set(CMAKE_C_FLAGS_INIT "-fPIC -fms-extensions")
set(CMAKE_CXX_FLAGS_INIT
  "-fPIC -fms-extensions -stdlib=libc++ -nostdinc++ -isystem \"${libcxx}/include\" -isystem \"${libcxx}/include/c++/v1\"")
set(CMAKE_CXX_STANDARD_LIBRARIES
  "\"${lib_dir}/libc++.a\" \"${lib_dir}/libc++abi.a\" -lm -lpthread -ldl")
foreach(kind EXE MODULE SHARED)
  set(CMAKE_${kind}_LINKER_FLAGS_INIT "-fuse-ld=lld -L\"${lib_dir}\"")
endforeach()
# Keep each shared library from interposing its static libc++/LLVM symbols
# on the other shader libraries or on Unreal itself.
string(APPEND CMAKE_SHARED_LINKER_FLAGS_INIT " -Wl,--exclude-libs,ALL")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
