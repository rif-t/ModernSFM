#include <Windows.h>

#include "modern_sfm/bridge_api.h"

#include <cstdint>
#include <cstring>
#include <string>

namespace {

constexpr DWORD kMaxMessageBytes = 64 * 1024;
constexpr int kOk = MSFM_OK;
constexpr int kInvalidArgument = MSFM_INVALID_ARGUMENT;
constexpr int kInvalidState = MSFM_INVALID_STATE;
constexpr int kWin32Error = MSFM_WIN32_ERROR;
constexpr int kTimeout = MSFM_TIMEOUT;
constexpr int kProtocolError = MSFM_PROTOCOL_ERROR;
constexpr int kBufferTooSmall = MSFM_BUFFER_TOO_SMALL;
constexpr int kWorkerExited = MSFM_WORKER_EXITED;
constexpr int kInternalError = MSFM_INTERNAL_ERROR;
struct Session {
    HANDLE pipe = INVALID_HANDLE_VALUE;
    HANDLE process = nullptr;
    DWORD process_id = 0;
    std::wstring pipe_name;
};

SRWLOCK g_lock = SRWLOCK_INIT;
Session g_session;
std::string g_error = "No error";
unsigned long g_pipe_counter = 0;

void set_error(const std::string& message) {
    g_error = message;
}

std::string win32_message(const char* operation, DWORD error) {
    return std::string(operation) + " failed (Win32 error " + std::to_string(error) + ")";
}

void close_handles(Session& session, bool terminate_worker) {
    if (session.pipe != INVALID_HANDLE_VALUE) {
        CancelIoEx(session.pipe, nullptr);
        CloseHandle(session.pipe);
        session.pipe = INVALID_HANDLE_VALUE;
    }
    if (session.process != nullptr) {
        if (terminate_worker && WaitForSingleObject(session.process, 0) == WAIT_TIMEOUT) {
            TerminateProcess(session.process, 0x4D53464D);
        }
        CloseHandle(session.process);
        session.process = nullptr;
    }
    session.process_id = 0;
    session.pipe_name.clear();
}

void reset_session(bool terminate_worker) {
    close_handles(g_session, terminate_worker);
}

bool wait_io(HANDLE pipe, OVERLAPPED& overlapped, DWORD timeout_ms,
             DWORD& transferred, const char* operation) {
    const DWORD wait_result = WaitForSingleObject(overlapped.hEvent, timeout_ms);
    if (wait_result == WAIT_TIMEOUT) {
        CancelIoEx(pipe, &overlapped);
        WaitForSingleObject(overlapped.hEvent, INFINITE);
        DWORD ignored = 0;
        GetOverlappedResult(pipe, &overlapped, &ignored, FALSE);
        SetLastError(ERROR_TIMEOUT);
        set_error(std::string(operation) + " timed out");
        return false;
    }
    if (wait_result != WAIT_OBJECT_0) {
        const DWORD error = GetLastError();
        CancelIoEx(pipe, &overlapped);
        set_error(win32_message(operation, error));
        return false;
    }
    if (!GetOverlappedResult(pipe, &overlapped, &transferred, FALSE)) {
        const DWORD error = GetLastError();
        set_error(error == ERROR_BROKEN_PIPE ? "Worker closed the named pipe" : win32_message(operation, error));
        return false;
    }
    return true;
}

bool io_exact(bool write, void* buffer, DWORD size, DWORD timeout_ms) {
    auto* cursor = static_cast<unsigned char*>(buffer);
    DWORD remaining = size;
    while (remaining != 0) {
        OVERLAPPED overlapped{};
        overlapped.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (overlapped.hEvent == nullptr) {
            set_error(win32_message("CreateEventW", GetLastError()));
            return false;
        }

        DWORD transferred = 0;
        const BOOL completed = write
            ? WriteFile(g_session.pipe, cursor, remaining, &transferred, &overlapped)
            : ReadFile(g_session.pipe, cursor, remaining, &transferred, &overlapped);
        if (!completed) {
            const DWORD error = GetLastError();
            if (error != ERROR_IO_PENDING) {
                CloseHandle(overlapped.hEvent);
                set_error(error == ERROR_BROKEN_PIPE
                              ? "Worker closed the named pipe"
                              : win32_message(write ? "WriteFile" : "ReadFile", error));
                return false;
            }
            if (!wait_io(g_session.pipe, overlapped, timeout_ms, transferred,
                         write ? "Named pipe write" : "Named pipe read")) {
                CloseHandle(overlapped.hEvent);
                return false;
            }
        }
        CloseHandle(overlapped.hEvent);
        if (transferred == 0) {
            set_error("Named pipe completed a zero-byte transfer");
            return false;
        }
        cursor += transferred;
        remaining -= transferred;
    }
    return true;
}

bool connect_worker(DWORD timeout_ms) {
    OVERLAPPED overlapped{};
    overlapped.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (overlapped.hEvent == nullptr) {
        set_error(win32_message("CreateEventW", GetLastError()));
        return false;
    }

    BOOL connected = ConnectNamedPipe(g_session.pipe, &overlapped);
    if (!connected) {
        const DWORD error = GetLastError();
        if (error == ERROR_PIPE_CONNECTED) {
            connected = TRUE;
        } else if (error == ERROR_IO_PENDING) {
            DWORD transferred = 0;
            connected = wait_io(g_session.pipe, overlapped, timeout_ms, transferred,
                                "Worker named-pipe connection") ? TRUE : FALSE;
        } else {
            set_error(win32_message("ConnectNamedPipe", error));
        }
    }
    CloseHandle(overlapped.hEvent);
    return connected != FALSE;
}

std::wstring quote_argument(const std::wstring& value) {
    std::wstring result = L"\"";
    for (wchar_t ch : value) {
        if (ch == L'\"') {
            result += L'\\';
        }
        result += ch;
    }
    result += L'\"';
    return result;
}

int request_locked(const char* request, DWORD request_size, char* response,
                   DWORD response_capacity, DWORD* response_size, DWORD timeout_ms) {
    if (g_session.pipe == INVALID_HANDLE_VALUE || g_session.process == nullptr) {
        set_error("Worker is not running");
        return kInvalidState;
    }
    if (request == nullptr || response_size == nullptr ||
        (response == nullptr && response_capacity != 0) || request_size > kMaxMessageBytes) {
        set_error("Invalid request arguments or message exceeds 64 KiB");
        return kInvalidArgument;
    }
    *response_size = 0;
    if (WaitForSingleObject(g_session.process, 0) == WAIT_OBJECT_0) {
        set_error("Worker process exited unexpectedly");
        reset_session(false);
        return kWorkerExited;
    }

    if (!io_exact(true, &request_size, sizeof(request_size), timeout_ms) ||
        (request_size != 0 && !io_exact(true, const_cast<char*>(request), request_size, timeout_ms))) {
        reset_session(true);
        return g_error.find("timed out") != std::string::npos ? kTimeout : kWin32Error;
    }

    DWORD received_size = 0;
    if (!io_exact(false, &received_size, sizeof(received_size), timeout_ms)) {
        reset_session(true);
        return g_error.find("timed out") != std::string::npos ? kTimeout : kWorkerExited;
    }
    if (received_size > kMaxMessageBytes) {
        set_error("Worker response exceeded the 64 KiB protocol limit");
        reset_session(true);
        return kProtocolError;
    }
    if (received_size > response_capacity) {
        set_error("Response buffer is too small");
        reset_session(true);
        return kBufferTooSmall;
    }
    if (received_size != 0 && !io_exact(false, response, received_size, timeout_ms)) {
        reset_session(true);
        return g_error.find("timed out") != std::string::npos ? kTimeout : kWorkerExited;
    }
    *response_size = received_size;
    set_error("No error");
    return kOk;
}

}  // namespace

int __cdecl msfm_abi_version() {
    return 1;
}

int __cdecl msfm_start(const wchar_t* worker_path, DWORD timeout_ms) {
    AcquireSRWLockExclusive(&g_lock);
    try {
        if (worker_path == nullptr || worker_path[0] == L'\0' || timeout_ms == 0) {
            set_error("Worker path and nonzero timeout are required");
            ReleaseSRWLockExclusive(&g_lock);
            return kInvalidArgument;
        }
        if (g_session.process != nullptr) {
            set_error("Worker is already running");
            ReleaseSRWLockExclusive(&g_lock);
            return kInvalidState;
        }

        g_session.pipe_name = L"\\\\.\\pipe\\ModernSFM_" + std::to_wstring(GetCurrentProcessId()) +
                              L"_" + std::to_wstring(GetTickCount64()) + L"_" +
                              std::to_wstring(++g_pipe_counter);
        g_session.pipe = CreateNamedPipeW(g_session.pipe_name.c_str(),
                                          PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                                          PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
                                              PIPE_REJECT_REMOTE_CLIENTS,
                                          1, kMaxMessageBytes + 4, kMaxMessageBytes + 4,
                                          timeout_ms, nullptr);
        if (g_session.pipe == INVALID_HANDLE_VALUE) {
            set_error(win32_message("CreateNamedPipeW", GetLastError()));
            reset_session(false);
            ReleaseSRWLockExclusive(&g_lock);
            return kWin32Error;
        }

        std::wstring command = quote_argument(worker_path) + L" --pipe " + quote_argument(g_session.pipe_name);
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(worker_path, &command[0], nullptr, nullptr, FALSE,
                            CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
            set_error(win32_message("CreateProcessW", GetLastError()));
            reset_session(false);
            ReleaseSRWLockExclusive(&g_lock);
            return kWin32Error;
        }
        CloseHandle(process.hThread);
        g_session.process = process.hProcess;
        g_session.process_id = process.dwProcessId;

        if (!connect_worker(timeout_ms)) {
            const int result = g_error.find("timed out") != std::string::npos ? kTimeout : kWin32Error;
            reset_session(true);
            ReleaseSRWLockExclusive(&g_lock);
            return result;
        }

        set_error("No error");
        ReleaseSRWLockExclusive(&g_lock);
        return kOk;
    } catch (...) {
        set_error("Unexpected C++ exception in msfm_start");
        reset_session(true);
        ReleaseSRWLockExclusive(&g_lock);
        return kInternalError;
    }
}

int __cdecl msfm_request(const char* request, DWORD request_size, char* response,
                         DWORD response_capacity, DWORD* response_size, DWORD timeout_ms) {
    AcquireSRWLockExclusive(&g_lock);
    try {
        const int result = request_locked(request, request_size, response, response_capacity,
                                          response_size, timeout_ms);
        ReleaseSRWLockExclusive(&g_lock);
        return result;
    } catch (...) {
        set_error("Unexpected C++ exception in msfm_request");
        reset_session(true);
        ReleaseSRWLockExclusive(&g_lock);
        return kInternalError;
    }
}

int __cdecl msfm_shutdown(DWORD timeout_ms) {
    AcquireSRWLockExclusive(&g_lock);
    try {
        if (g_session.process == nullptr) {
            set_error("Worker is not running");
            ReleaseSRWLockExclusive(&g_lock);
            return kInvalidState;
        }

        char response[16] = {};
        DWORD response_size = 0;
        const int request_result = request_locked("QUIT", 4, response, sizeof(response),
                                                  &response_size, timeout_ms);
        if (request_result != kOk || std::string(response, response_size) != "BYE") {
            if (request_result == kOk) {
                set_error("Worker returned an invalid shutdown response");
            }
            reset_session(true);
            ReleaseSRWLockExclusive(&g_lock);
            return request_result == kOk ? kProtocolError : request_result;
        }

        const DWORD wait_result = WaitForSingleObject(g_session.process, timeout_ms);
        if (wait_result != WAIT_OBJECT_0) {
            set_error(wait_result == WAIT_TIMEOUT ? "Worker shutdown timed out"
                                                  : win32_message("WaitForSingleObject", GetLastError()));
            reset_session(true);
            ReleaseSRWLockExclusive(&g_lock);
            return wait_result == WAIT_TIMEOUT ? kTimeout : kWin32Error;
        }
        reset_session(false);
        set_error("No error");
        ReleaseSRWLockExclusive(&g_lock);
        return kOk;
    } catch (...) {
        set_error("Unexpected C++ exception in msfm_shutdown");
        reset_session(true);
        ReleaseSRWLockExclusive(&g_lock);
        return kInternalError;
    }
}

int __cdecl msfm_last_error(char* buffer, DWORD capacity) {
    AcquireSRWLockExclusive(&g_lock);
    if (buffer == nullptr || capacity == 0) {
        ReleaseSRWLockExclusive(&g_lock);
        return kInvalidArgument;
    }
    const std::size_t copy_size = (g_error.size() < capacity - 1) ? g_error.size() : capacity - 1;
    std::memcpy(buffer, g_error.data(), copy_size);
    buffer[copy_size] = '\0';
    ReleaseSRWLockExclusive(&g_lock);
    return kOk;
}
