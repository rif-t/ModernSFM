#include "modern_sfm/worker_jobs.hpp"

#include "modern_sfm/exr_pack.hpp"
#include "modern_sfm/pfm.hpp"

#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

namespace modern_sfm {
namespace {

constexpr std::uint64_t kMiB = 1024ULL * 1024ULL;

}  // namespace

std::string pack_pfm_pair(const std::filesystem::path& beauty_path,
                          const std::filesystem::path& depth_path,
                          const std::filesystem::path& output_path,
                          std::uint64_t max_memory_mib) {
    if (max_memory_mib == 0 || max_memory_mib > std::numeric_limits<std::uint64_t>::max() / kMiB) {
        throw std::runtime_error("memory budget is invalid or too large");
    }
    const std::uint64_t budget_bytes = max_memory_mib * kMiB;

    PfmImage beauty = read_pfm(beauty_path, budget_bytes);
    const std::uint64_t beauty_bytes = static_cast<std::uint64_t>(beauty.pixels.size()) * sizeof(float);
    if (beauty_bytes >= budget_bytes) {
        throw std::runtime_error("beauty input leaves no memory budget for depth input");
    }
    PfmImage depth = read_pfm(depth_path, budget_bytes - beauty_bytes);
    if (beauty.channels != 3) {
        throw std::runtime_error("beauty input must be RGB PFM (PF)");
    }
    if (depth.channels != 1) {
        throw std::runtime_error("depth input must be single-channel PFM (Pf)");
    }
    if (beauty.width != depth.width || beauty.height != depth.height) {
        throw std::runtime_error("beauty/depth PFM dimensions do not match");
    }

    const std::uint64_t pixel_count = static_cast<std::uint64_t>(beauty.width) * beauty.height;
    if (pixel_count > std::numeric_limits<std::uint64_t>::max() / (4ULL * sizeof(float))) {
        throw std::runtime_error("paired PFM working set size overflows");
    }
    const std::uint64_t image_bytes = pixel_count * 4ULL * sizeof(float);
    const std::uint64_t row_bytes = static_cast<std::uint64_t>(beauty.width) * 7ULL * sizeof(float);
    if (image_bytes > std::numeric_limits<std::uint64_t>::max() - row_bytes) {
        throw std::runtime_error("paired PFM working set size overflows");
    }
    if (image_bytes + row_bytes > budget_bytes) {
        throw std::runtime_error("paired PFM working set exceeds configured memory budget");
    }

    write_and_validate_exr(beauty, depth, output_path);
    return "Validated EXR: " + output_path.string() + " (" +
           std::to_string(beauty.width) + "x" + std::to_string(beauty.height) +
           ", FLOAT, ZIP; channels raw-sfm.R/G/B, depth.Z, image-final.R/G/B). "
           "Source PFMs were retained.";
}

}  // namespace modern_sfm
