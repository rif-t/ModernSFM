#pragma once

#ifndef _WIN32
#error ModernSFM Bridge ABI is Windows-only
#endif

#include <Windows.h>

#ifdef __cplusplus
extern "C" {
#endif

enum modern_sfm_bridge_result {
    MSFM_OK = 0,
    MSFM_INVALID_ARGUMENT = 1,
    MSFM_INVALID_STATE = 2,
    MSFM_WIN32_ERROR = 3,
    MSFM_TIMEOUT = 4,
    MSFM_PROTOCOL_ERROR = 5,
    MSFM_BUFFER_TOO_SMALL = 6,
    MSFM_WORKER_EXITED = 7,
    MSFM_INTERNAL_ERROR = 8
};

__declspec(dllexport) int __cdecl msfm_abi_version(void);
__declspec(dllexport) int __cdecl msfm_start(const wchar_t* worker_path, DWORD timeout_ms);
__declspec(dllexport) int __cdecl msfm_request(const char* request,
                                               DWORD request_size,
                                               char* response,
                                               DWORD response_capacity,
                                               DWORD* response_size,
                                               DWORD timeout_ms);
__declspec(dllexport) int __cdecl msfm_shutdown(DWORD timeout_ms);
__declspec(dllexport) int __cdecl msfm_last_error(char* buffer, DWORD capacity);

#ifdef __cplusplus
}
#endif
