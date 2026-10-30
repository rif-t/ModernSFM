#include "modern_sfm/exr_pack.hpp"

#include <OpenEXR/ImfChannelList.h>
#include <OpenEXR/ImfCompression.h>
#include <OpenEXR/ImfFrameBuffer.h>
#include <OpenEXR/ImfHeader.h>
#include <OpenEXR/ImfInputFile.h>
#include <OpenEXR/ImfOutputFile.h>
#include <OpenEXR/ImfStringAttribute.h>

#include <array>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace modern_sfm {
namespace {

namespace Imf = OPENEXR_IMF_NAMESPACE;

std::string channel_name(const std::string& layer, const char* component) {
    return layer + "." + component;
}

void add_rgb_slices(Imf::FrameBuffer& buffer,
                    const std::string& layer,
                    const float* pixels,
                    std::uint32_t width,
                    std::uint32_t channels) {
    const std::array<const char*, 3> components = {"R", "G", "B"};
    const std::size_t pixel_stride = static_cast<std::size_t>(channels) * sizeof(float);
    const std::size_t row_stride = static_cast<std::size_t>(width) * pixel_stride;
    for (std::size_t component = 0; component < components.size(); ++component) {
        buffer.insert(channel_name(layer, components[component]),
                      Imf::Slice(Imf::FLOAT,
                                 reinterpret_cast<char*>(const_cast<float*>(pixels + component)),
                                 pixel_stride,
                                 row_stride));
    }
}

void add_rgb_row_slices(Imf::FrameBuffer& buffer,
                        const std::string& layer,
                        float* row,
                        std::uint32_t width) {
    const std::array<const char*, 3> components = {"R", "G", "B"};
    for (std::size_t component = 0; component < components.size(); ++component) {
        buffer.insert(channel_name(layer, components[component]),
                      Imf::Slice(Imf::FLOAT,
                                 reinterpret_cast<char*>(row + component),
                                 sizeof(float) * 3,
                                 0));
    }
    (void)width;
}

void compare_row(const float* expected, const float* actual, std::size_t count,
                 const std::string& label, std::uint32_t y) {
    if (std::memcmp(expected, actual, count * sizeof(float)) != 0) {
        throw std::runtime_error("EXR validation mismatch in " + label + " at row " + std::to_string(y));
    }
}

void validate_exr(const std::filesystem::path& path,
                  const PfmImage& beauty,
                  const PfmImage& depth) {
    Imf::InputFile file(path.string().c_str());
    const Imf::Header& header = file.header();
    const auto& window = header.dataWindow();
    if (window.min.x != 0 || window.min.y != 0 ||
        window.max.x != static_cast<int>(beauty.width) - 1 ||
        window.max.y != static_cast<int>(beauty.height) - 1) {
        throw std::runtime_error("EXR validation failed: data window does not match source dimensions");
    }

    const std::array<std::string, 7> required = {
        "raw-sfm.R", "raw-sfm.G", "raw-sfm.B", "depth.Z",
        "image-final.R", "image-final.G", "image-final.B"};
    for (const auto& name : required) {
        const Imf::Channel* channel = header.channels().findChannel(name.c_str());
        if (channel == nullptr) {
            throw std::runtime_error("EXR validation failed: missing channel " + name);
        }
        if (channel->type != Imf::FLOAT) {
            throw std::runtime_error("EXR validation failed: channel is not FLOAT: " + name);
        }
    }

    std::vector<float> raw_row(static_cast<std::size_t>(beauty.width) * 3);
    std::vector<float> final_row(static_cast<std::size_t>(beauty.width) * 3);
    std::vector<float> depth_row(beauty.width);
    Imf::FrameBuffer frame_buffer;
    add_rgb_row_slices(frame_buffer, "raw-sfm", raw_row.data(), beauty.width);
    add_rgb_row_slices(frame_buffer, "image-final", final_row.data(), beauty.width);
    frame_buffer.insert("depth.Z", Imf::Slice(Imf::FLOAT,
                                              reinterpret_cast<char*>(depth_row.data()),
                                              sizeof(float),
                                              0));
    file.setFrameBuffer(frame_buffer);

    for (std::uint32_t y = 0; y < beauty.height; ++y) {
        file.readPixels(static_cast<int>(y), static_cast<int>(y));
        const std::size_t beauty_offset = static_cast<std::size_t>(y) * beauty.width * 3;
        const std::size_t depth_offset = static_cast<std::size_t>(y) * depth.width;
        compare_row(beauty.pixels.data() + beauty_offset, raw_row.data(), raw_row.size(), "raw-sfm", y);
        compare_row(beauty.pixels.data() + beauty_offset, final_row.data(), final_row.size(), "image-final", y);
        compare_row(depth.pixels.data() + depth_offset, depth_row.data(), depth_row.size(), "depth.Z", y);
    }
}

}  // namespace

void write_and_validate_exr(const PfmImage& beauty,
                            const PfmImage& depth,
                            const std::filesystem::path& output_path) {
    if (beauty.channels != 3 || depth.channels != 1 ||
        beauty.width != depth.width || beauty.height != depth.height) {
        throw std::runtime_error("internal error: incompatible beauty/depth images passed to EXR writer");
    }
    if (std::filesystem::exists(output_path)) {
        throw std::runtime_error("output already exists; refusing to overwrite: " + output_path.string());
    }

    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    std::filesystem::path temporary_path = output_path;
    temporary_path += ".tmp-" + std::to_string(nonce);

    try {
        Imf::Header header(static_cast<int>(beauty.width), static_cast<int>(beauty.height));
        header.compression() = Imf::ZIP_COMPRESSION;
        header.channels().insert("raw-sfm.R", Imf::Channel(Imf::FLOAT));
        header.channels().insert("raw-sfm.G", Imf::Channel(Imf::FLOAT));
        header.channels().insert("raw-sfm.B", Imf::Channel(Imf::FLOAT));
        header.channels().insert("depth.Z", Imf::Channel(Imf::FLOAT));
        header.channels().insert("image-final.R", Imf::Channel(Imf::FLOAT));
        header.channels().insert("image-final.G", Imf::Channel(Imf::FLOAT));
        header.channels().insert("image-final.B", Imf::Channel(Imf::FLOAT));
        header.insert("comments", Imf::StringAttribute(
            "image-final is an identity copy of raw-sfm; source color space is unverified."));

        {
            Imf::OutputFile file(temporary_path.string().c_str(), header);
            Imf::FrameBuffer frame_buffer;
            add_rgb_slices(frame_buffer, "raw-sfm", beauty.pixels.data(), beauty.width, beauty.channels);
            add_rgb_slices(frame_buffer, "image-final", beauty.pixels.data(), beauty.width, beauty.channels);
            frame_buffer.insert("depth.Z", Imf::Slice(Imf::FLOAT,
                                                       reinterpret_cast<char*>(const_cast<float*>(depth.pixels.data())),
                                                       sizeof(float),
                                                       static_cast<std::size_t>(depth.width) * sizeof(float)));
            file.setFrameBuffer(frame_buffer);
            file.writePixels(static_cast<int>(beauty.height));
        }

        validate_exr(temporary_path, beauty, depth);
        std::filesystem::rename(temporary_path, output_path);
    } catch (...) {
        std::error_code ignored;
        std::filesystem::remove(temporary_path, ignored);
        throw;
    }
}

}  // namespace modern_sfm
