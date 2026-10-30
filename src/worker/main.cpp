#include "modern_sfm/exr_pack.hpp"
#include "modern_sfm/pfm.hpp"
#include "modern_sfm/worker_jobs.hpp"
#include "pipe_worker.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::uint64_t kDefaultMemoryBudgetMiB = 4096;

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
    return arguments;
}

int run(int argc, char* argv[]) {
    if (argc == 3 && std::string(argv[1]) == "--pipe") {
        const std::string narrow_pipe_name = argv[2];
        return run_pipe_worker(std::wstring(narrow_pipe_name.begin(), narrow_pipe_name.end()));
    }
    const Arguments arguments = parse_arguments(argc, argv);
    std::cout << modern_sfm::pack_pfm_pair(arguments.beauty, arguments.depth,
                                           arguments.output, arguments.max_memory_mib) << '\n';
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
