#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace modern_sfm {

std::string pack_pfm_pair(const std::filesystem::path& beauty_path,
                          const std::filesystem::path& depth_path,
                          const std::filesystem::path& output_path,
                          std::uint64_t max_memory_mib);

}  // namespace modern_sfm
