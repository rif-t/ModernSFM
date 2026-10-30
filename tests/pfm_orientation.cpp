#include "modern_sfm/pfm.hpp"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

int main() {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const std::filesystem::path fixture =
        std::filesystem::temp_directory_path() / ("modern_sfm_orientation_" + std::to_string(nonce) + ".pfm");

    try {
        {
            std::ofstream output(fixture, std::ios::binary);
            if (!output) {
                throw std::runtime_error("could not create orientation fixture");
            }
            output << "PF\n2 2\n-1.0\n";
            // The first payload row is the top row in the observed SFM output.
            const float top_to_bottom[] = {
                1.0F, 2.0F, 3.0F, 4.0F, 5.0F, 6.0F,
                7.0F, 8.0F, 9.0F, 10.0F, 11.0F, 12.0F};
            output.write(reinterpret_cast<const char*>(top_to_bottom), sizeof(top_to_bottom));
            if (!output) {
                throw std::runtime_error("could not write orientation fixture");
            }
        }

        const modern_sfm::PfmImage image = modern_sfm::read_pfm(fixture, 1024 * 1024);
        if (image.width != 2 || image.height != 2 || image.channels != 3 ||
            image.pixels[0] != 1.0F || image.pixels[3] != 4.0F || image.pixels[6] != 7.0F) {
            throw std::runtime_error("PFM parser changed SFM top-to-bottom row order");
        }

        std::filesystem::remove(fixture);
        std::cout << "PFM top-to-bottom orientation check passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::error_code ignored;
        std::filesystem::remove(fixture, ignored);
        std::cerr << error.what() << '\n';
        return 1;
    }
}
