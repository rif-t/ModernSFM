#include "modern_sfm/exr_pack.hpp"
#include "modern_sfm/pfm.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::uint64_t kDefaultMemoryBudgetMiB = 4096;
constexpr std::uint64_t kMiB = 1024ULL * 1024ULL;

void print_usage() {
    std::cerr << "Usage: modern_sfm_worker pack --beauty <RGB.pfm> --depth <Z.pfm> "
                 "--output <result.exr> [--max-memory-mib <MiB>]\n";
}

std::uint64_t parse_positive_integer(const std::string& value, const char* option) {
    std::size_t parsed = 0;
    unsigned long long number = 0;
    try {
        number = std::stoull(value, &parsed, 10);
    } catch (...) {
        throw std::runtime_error(std::string("invalid value for ") + option);
    }
    if (parsed != value.size() || number == 0) {
        throw std::runtime_error(std::string("invalid value for ") + option);
    }
    return static_cast<std::uint64_t>(number);
}

struct Arguments {
    std::filesystem::path beauty;
    std::filesystem::path depth;
    std::filesystem::path output;
    std::uint64_t max_memory_mib = kDefaultMemoryBudgetMiB;
};

Arguments parse_arguments(int argc, char* argv[]) {
    if (argc < 2 || std::string(argv[1]) != "pack") {
        throw std::runtime_error("expected the 'pack' command");
    }
    Arguments arguments;
    bool have_beauty = false;
    bool have_depth = false;
    bool have_output = false;
    for (int i = 2; i < argc; ++i) {
        const std::string option = argv[i];
        if (i + 1 >= argc) {
            throw std::runtime_error("missing value for " + option);
        }
        const std::string value = argv[++i];
        if (option == "--beauty" && !have_beauty) {
            arguments.beauty = value;
            have_beauty = true;
        } else if (option == "--depth" && !have_depth) {
            arguments.depth = value;
            have_depth = true;
        } else if (option == "--output" && !have_output) {
            arguments.output = value;
            have_output = true;
        } else if (option == "--max-memory-mib") {
            arguments.max_memory_mib = parse_positive_integer(value, "--max-memory-mib");
        } else {
            throw std::runtime_error("unknown or repeated option: " + option);
        }
    }
    if (!have_beauty || !have_depth || !have_output) {
        throw std::runtime_error("--beauty, --depth, and --output are required");
    }
    if (arguments.max_memory_mib > std::numeric_limits<std::uint64_t>::max() / kMiB) {
        throw std::runtime_error("--max-memory-mib is too large");
    }
    return arguments;
}

int run(int argc, char* argv[]) {
    const Arguments arguments = parse_arguments(argc, argv);
    const std::uint64_t budget_bytes = arguments.max_memory_mib * kMiB;

    modern_sfm::PfmImage beauty = modern_sfm::read_pfm(arguments.beauty, budget_bytes);
    const std::uint64_t beauty_bytes = static_cast<std::uint64_t>(beauty.pixels.size()) * sizeof(float);
    if (beauty_bytes >= budget_bytes) {
        throw std::runtime_error("beauty input leaves no memory budget for depth input");
    }
    modern_sfm::PfmImage depth = modern_sfm::read_pfm(arguments.depth, budget_bytes - beauty_bytes);
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
    const std::uint64_t app_bytes = image_bytes + row_bytes;
    if (app_bytes > budget_bytes) {
        throw std::runtime_error("paired PFM working set exceeds configured memory budget");
    }

    modern_sfm::write_and_validate_exr(beauty, depth, arguments.output);
    std::cout << "Validated EXR: " << arguments.output.string() << " ("
              << beauty.width << "x" << beauty.height << ", FLOAT, ZIP)\n";
    std::cout << "Channels: raw-sfm.R/G/B, depth.Z, image-final.R/G/B\n";
    std::cout << "image-final is an identity copy; source PFMs were retained.\n";
    return 0;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        return run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "modern_sfm_worker: " << error.what() << '\n';
        print_usage();
        return 2;
    }
}
