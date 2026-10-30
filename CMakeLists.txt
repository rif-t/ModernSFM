cmake_minimum_required(VERSION 3.25)

project(ModernSFM VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(MODERNSFM_BUILD_WORKER "Build the standalone x64 Worker" ON)
option(MODERNSFM_BUILD_BRIDGE "Build the x86 SFM Bridge DLL" ON)

if(MODERNSFM_BUILD_WORKER)
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
        message(FATAL_ERROR "ModernSFM Worker must be configured for 64-bit (x64)")
    endif()
    find_package(OpenEXR CONFIG REQUIRED)

    add_executable(modern_sfm_worker
        src/worker/main.cpp
        src/worker/pfm.cpp
        src/worker/exr_pack.cpp
        src/worker/pack_job.cpp
        src/worker/pipe_worker.cpp
    )
    target_include_directories(modern_sfm_worker PRIVATE include src/worker)
    target_link_libraries(modern_sfm_worker PRIVATE OpenEXR::OpenEXR)
    target_compile_definitions(modern_sfm_worker PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    if(MSVC)
        target_compile_options(modern_sfm_worker PRIVATE /W4 /permissive- /EHsc)
    endif()
endif()

if(MODERNSFM_BUILD_BRIDGE)
    if(NOT WIN32)
        message(FATAL_ERROR "ModernSFM Bridge currently requires Windows")
    endif()
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
        message(FATAL_ERROR "ModernSFM Bridge must be configured for 32-bit (Win32)")
    endif()
    add_library(modern_sfm_bridge SHARED src/bridge/modern_sfm_bridge.cpp)
    target_include_directories(modern_sfm_bridge PRIVATE include)
    target_compile_definitions(modern_sfm_bridge PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    if(MSVC)
        target_compile_options(modern_sfm_bridge PRIVATE /W4 /permissive- /EHsc)
    endif()
endif()

include(CTest)
if(BUILD_TESTING AND MODERNSFM_BUILD_WORKER)
    add_executable(pfm_orientation_test
        tests/pfm_orientation.cpp
        src/worker/pfm.cpp
    )
    target_include_directories(pfm_orientation_test PRIVATE include)
    if(MSVC)
        target_compile_options(pfm_orientation_test PRIVATE /W4 /permissive- /EHsc)
        target_compile_definitions(pfm_orientation_test PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    endif()
    add_test(NAME pfm_orientation COMMAND pfm_orientation_test)

    find_package(Python3 COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        add_test(
            NAME render_job_lifecycle_smoke
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/job_lifecycle_smoke.py"
        )
        add_test(
            NAME worker_smoke
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/worker_smoke.py"
                    "$<TARGET_FILE:modern_sfm_worker>"
        )
    endif()
endif()
