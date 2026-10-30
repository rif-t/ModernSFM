#include <Windows.h>

#include "pipe_worker.hpp"

#include <cstdint>
#include <iostream>
#include <string>

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
