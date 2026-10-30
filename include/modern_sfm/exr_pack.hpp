#pragma once

#include "modern_sfm/pfm.hpp"

#include <filesystem>

namespace modern_sfm {

void write_and_validate_exr(const PfmImage& beauty,
                            const PfmImage& depth,
                            const std::filesystem::path& output_path);

}  // namespace modern_sfm
