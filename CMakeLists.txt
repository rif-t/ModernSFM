cmake_minimum_required(VERSION 3.25)

project(ModernSFMWorker VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(OpenEXR CONFIG REQUIRED)

add_executable(modern_sfm_worker
    src/worker/main.cpp
    src/worker/pfm.cpp
    src/worker/exr_pack.cpp
)

target_include_directories(modern_sfm_worker PRIVATE include)
target_link_libraries(modern_sfm_worker PRIVATE OpenEXR::OpenEXR)

if(MSVC)
    target_compile_options(modern_sfm_worker PRIVATE /W4 /permissive- /EHsc)
    target_compile_definitions(modern_sfm_worker PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
endif()

include(CTest)
find_package(Python3 COMPONENTS Interpreter)
if(BUILD_TESTING AND Python3_Interpreter_FOUND)
    add_test(
        NAME worker_smoke
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/worker_smoke.py"
                "$<TARGET_FILE:modern_sfm_worker>"
    )
endif()
