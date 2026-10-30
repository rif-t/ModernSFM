#include <Windows.h>

#include "pipe_worker.hpp"
#include "modern_sfm/worker_jobs.hpp"

#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <filesystem>

namespace {

constexpr std::uint32_t kMaxMessageBytes = 64 * 1024;

bool read_exact(HANDLE pipe, void* buffer, std::uint32_t size) {
    auto* next = static_cast<unsigned char*>(buffer);
    std::uint32_t remaining = size;
    while (remaining != 0) {
        DWORD read = 0;
        if (!ReadFile(pipe, next, remaining, &read, nullptr) || read == 0) {
            return false;
        }
        next += read;
        remaining -= read;
    }
    return true;
}

bool write_exact(HANDLE pipe, const void* buffer, std::uint32_t size) {
    const auto* next = static_cast<const unsigned char*>(buffer);
    std::uint32_t remaining = size;
    while (remaining != 0) {
        DWORD written = 0;
        if (!WriteFile(pipe, next, remaining, &written, nullptr) || written == 0) {
            return false;
        }
        next += written;
        remaining -= written;
    }
    return true;
}

bool read_frame(HANDLE pipe, std::string& message) {
    std::uint32_t size = 0;
    if (!read_exact(pipe, &size, sizeof(size)) || size > kMaxMessageBytes) {
        return false;
    }
    message.resize(size);
    return size == 0 || read_exact(pipe, &message[0], size);
}

bool write_frame(HANDLE pipe, const std::string& message) {
    if (message.size() > kMaxMessageBytes) {
        return false;
    }
    const auto size = static_cast<std::uint32_t>(message.size());
    return write_exact(pipe, &size, sizeof(size)) &&
           (size == 0 || write_exact(pipe, message.data(), size));
}

std::uint32_t read_u32(const std::string& data, std::size_t offset) {
    return static_cast<unsigned char>(data[offset]) |
           (static_cast<std::uint32_t>(static_cast<unsigned char>(data[offset + 1])) << 8) |
           (static_cast<std::uint32_t>(static_cast<unsigned char>(data[offset + 2])) << 16) |
           (static_cast<std::uint32_t>(static_cast<unsigned char>(data[offset + 3])) << 24);
}

std::uint64_t read_u64(const std::string& data, std::size_t offset) {
    return static_cast<std::uint64_t>(read_u32(data, offset)) |
           (static_cast<std::uint64_t>(read_u32(data, offset + 4)) << 32);
}

void append_u32(std::string& data, std::uint32_t value) {
    for (unsigned int i = 0; i < 4; ++i) data.push_back(static_cast<char>(value >> (8 * i)));
}

std::string pack_response(const std::string& request) {
    std::uint32_t status = 1;
    std::string message;
    try {
        constexpr std::size_t header_size = 24;
        if (request.size() < header_size || request.compare(0, 4, "PACK") != 0)
            throw std::runtime_error("Malformed PACK header");
        const auto beauty_size = read_u32(request, 4);
        const auto depth_size = read_u32(request, 8);
        const auto output_size = read_u32(request, 12);
        const auto memory_mib = read_u64(request, 16);
        constexpr std::uint32_t max_path_bytes = 32 * 1024;
        if (!beauty_size || !depth_size || !output_size ||
            beauty_size > max_path_bytes || depth_size > max_path_bytes || output_size > max_path_bytes ||
            memory_mib == 0)
            throw std::runtime_error("Invalid PACK path length or memory limit");
        const std::uint64_t expected = header_size + static_cast<std::uint64_t>(beauty_size) + depth_size + output_size;
        if (expected != request.size()) throw std::runtime_error("PACK payload length does not match header");
        std::size_t offset = header_size;
        auto take_path = [&](std::uint32_t size) {
            std::string value(request.data() + offset, size);
            offset += size;
            if (value.find('\0') != std::string::npos) throw std::runtime_error("PACK path contains NUL");
            return std::filesystem::u8path(value);
        };
        const auto beauty = take_path(beauty_size);
        const auto depth = take_path(depth_size);
        const auto output = take_path(output_size);
        message = modern_sfm::pack_pfm_pair(beauty, depth, output, memory_mib);
        status = 0;
    } catch (const std::exception& error) {
        message = error.what();
    } catch (...) {
        message = "Unknown PACK processing error";
    }
    if (message.size() > kMaxMessageBytes - 12) message.resize(kMaxMessageBytes - 12);
    std::string response("RSLT", 4);
    append_u32(response, status);
    append_u32(response, static_cast<std::uint32_t>(message.size()));
    response += message;
    return response;
}

HANDLE connect_to_bridge(const std::wstring& pipe_name) {
    const ULONGLONG deadline = GetTickCount64() + 15000;
    while (GetTickCount64() < deadline) {
        HANDLE pipe = CreateFileW(pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0,
                                  nullptr, OPEN_EXISTING, 0, nullptr);
        if (pipe != INVALID_HANDLE_VALUE) {
            DWORD mode = PIPE_READMODE_BYTE;
            if (!SetNamedPipeHandleState(pipe, &mode, nullptr, nullptr)) {
                CloseHandle(pipe);
                return INVALID_HANDLE_VALUE;
            }
            return pipe;
        }
        const DWORD error = GetLastError();
        if (error == ERROR_PIPE_BUSY) {
            WaitNamedPipeW(pipe_name.c_str(), 250);
        } else if (error == ERROR_FILE_NOT_FOUND) {
            Sleep(50);
        } else {
            return INVALID_HANDLE_VALUE;
        }
    }
    SetLastError(ERROR_TIMEOUT);
    return INVALID_HANDLE_VALUE;
}

}  // namespace

int run_pipe_worker(const std::wstring& pipe_name) {
    HANDLE pipe = connect_to_bridge(pipe_name);
    if (pipe == INVALID_HANDLE_VALUE) {
        std::cerr << "Worker could not connect to Bridge pipe (Win32 error "
                  << GetLastError() << ")\n";
        return 10;
    }

    for (;;) {
        std::string request;
        if (!read_frame(pipe, request)) {
            std::cerr << "Worker received a truncated or oversized frame\n";
            CloseHandle(pipe);
            return 11;
        }

        std::string response;
        bool should_exit = false;
        if (request == "PING") {
            response = "PONG";
        } else if (request.compare(0, 5, "ECHO ") == 0) {
            response.assign(request.begin() + 5, request.end());
        } else if (request == "QUIT") {
            response = "BYE";
            should_exit = true;
        } else if (request.compare(0, 4, "PACK") == 0) {
            response = pack_response(request);
        } else {
            response = "ERROR unknown command";
        }

        if (!write_frame(pipe, response)) {
            std::cerr << "Worker could not write response frame\n";
            CloseHandle(pipe);
            return 12;
        }
        if (should_exit) {
            break;
        }
    }

    FlushFileBuffers(pipe);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
    return 0;
}
