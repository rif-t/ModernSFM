#include "modern_sfm/pfm.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

namespace modern_sfm {
namespace {

std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return {};
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::string read_header_line(std::istream& input, const std::filesystem::path& path) {
    std::string line;
    while (std::getline(input, line)) {
        const auto comment = line.find('#');
        if (comment != std::string::npos) {
            line.erase(comment);
        }
        line = trim(std::move(line));
        if (!line.empty()) {
            return line;
        }
    }
    throw std::runtime_error("truncated PFM header: " + path.string());
}

std::pair<std::uint32_t, std::uint32_t> parse_dimensions(const std::string& line,
                                                         const std::filesystem::path& path) {
    std::istringstream stream(line);
    std::uint64_t width = 0;
    std::uint64_t height = 0;
    std::string extra;
    if (!(stream >> width >> height) || (stream >> extra) || width == 0 || height == 0 ||
        width > std::numeric_limits<std::uint32_t>::max() ||
        height > std::numeric_limits<std::uint32_t>::max()) {
        throw std::runtime_error("invalid PFM dimensions: " + path.string());
    }
    return {static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height)};
}

float parse_scale(const std::string& line, const std::filesystem::path& path) {
    std::istringstream stream(line);
    float scale = 0.0F;
    std::string extra;
    if (!(stream >> scale) || (stream >> extra) || !std::isfinite(scale) || scale == 0.0F) {
        throw std::runtime_error("invalid PFM scale: " + path.string());
    }
    return scale;
}

bool host_is_little_endian() {
    const std::uint16_t value = 1;
    return *reinterpret_cast<const std::uint8_t*>(&value) == 1;
}

std::uint32_t byte_swap(std::uint32_t value) {
    return ((value & 0x000000FFU) << 24U) |
           ((value & 0x0000FF00U) << 8U) |
           ((value & 0x00FF0000U) >> 8U) |
           ((value & 0xFF000000U) >> 24U);
}

std::uint64_t checked_mul(std::uint64_t a, std::uint64_t b, const std::filesystem::path& path) {
    if (a != 0 && b > std::numeric_limits<std::uint64_t>::max() / a) {
        throw std::runtime_error("PFM dimensions overflow: " + path.string());
    }
    return a * b;
}

}  // namespace

PfmImage read_pfm(const std::filesystem::path& path, std::uint64_t max_application_bytes) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open PFM: " + path.string());
    }

    const std::string magic = read_header_line(input, path);
    std::uint32_t channels = 0;
    if (magic == "PF") {
        channels = 3;
    } else if (magic == "Pf") {
        channels = 1;
    } else {
        throw std::runtime_error("unsupported PFM magic (expected PF or Pf): " + path.string());
    }

    const auto dimensions = parse_dimensions(read_header_line(input, path), path);
    const float signed_scale = parse_scale(read_header_line(input, path), path);
    const float scale = std::fabs(signed_scale);
    const bool file_is_little_endian = signed_scale < 0.0F;

    const std::uint64_t pixel_count = checked_mul(dimensions.first, dimensions.second, path);
    const std::uint64_t sample_count = checked_mul(pixel_count, channels, path);
    const std::uint64_t payload_bytes = checked_mul(sample_count, sizeof(float), path);
    const std::uint64_t row_bytes = checked_mul(
        checked_mul(dimensions.first, channels, path), sizeof(float), path);
    const std::uint64_t estimated_bytes = payload_bytes + row_bytes;
    if (estimated_bytes < payload_bytes || estimated_bytes > max_application_bytes ||
        sample_count > std::numeric_limits<std::size_t>::max()) {
        throw std::runtime_error("PFM exceeds configured memory budget: " + path.string());
    }

    const auto payload_start = input.tellg();
    if (payload_start < 0) {
        throw std::runtime_error("cannot locate PFM payload: " + path.string());
    }
    std::error_code size_error;
    const std::uint64_t file_size = std::filesystem::file_size(path, size_error);
    if (size_error || file_size < static_cast<std::uint64_t>(payload_start) ||
        file_size - static_cast<std::uint64_t>(payload_start) != payload_bytes) {
        throw std::runtime_error("PFM payload is incomplete or has unexpected trailing data: " + path.string());
    }

    PfmImage image;
    image.width = dimensions.first;
    image.height = dimensions.second;
    image.channels = channels;
    image.pixels.resize(static_cast<std::size_t>(sample_count));
    std::vector<std::uint32_t> row_words(static_cast<std::size_t>(dimensions.first) * channels);

    const bool swap_bytes = file_is_little_endian != host_is_little_endian();
    for (std::uint32_t file_y = 0; file_y < image.height; ++file_y) {
        input.read(reinterpret_cast<char*>(row_words.data()), static_cast<std::streamsize>(row_bytes));
        if (input.gcount() != static_cast<std::streamsize>(row_bytes)) {
            throw std::runtime_error("short read in PFM payload: " + path.string());
        }

        const std::uint32_t output_y = image.height - 1U - file_y;
        const std::size_t output_offset = static_cast<std::size_t>(output_y) * image.width * channels;
        for (std::size_t i = 0; i < row_words.size(); ++i) {
            std::uint32_t bits = swap_bytes ? byte_swap(row_words[i]) : row_words[i];
            float value = 0.0F;
            std::memcpy(&value, &bits, sizeof(value));
            if (!std::isfinite(value)) {
                throw std::runtime_error("PFM contains NaN or infinity: " + path.string());
            }
            value *= scale;
            if (!std::isfinite(value)) {
                throw std::runtime_error("PFM scale produced a non-finite value: " + path.string());
            }
            image.pixels[output_offset + i] = value;
        }
    }

    return image;
}

}  // namespace modern_sfm
