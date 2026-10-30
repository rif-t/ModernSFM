#pragma once

#include <cstdint>
#include <filesystem>
#include <vector>

namespace modern_sfm {

struct PfmImage {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t channels = 0;
    std::vector<float> pixels;
};

PfmImage read_pfm(const std::filesystem::path& path,
                  std::uint64_t max_application_bytes);

}  // namespace modern_sfm
