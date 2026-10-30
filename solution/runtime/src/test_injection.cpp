#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "log_file.h"

#ifdef PROBE_DLL
extern "C" DWORD WINAPI StartRuntime(void*) {
    FILE* file = open_log_file(L"chs_patch\\runtime_probe.log");
    if (!file) return ERROR_ACCESS_DENIED;
    fputs("INITIALIZED before host entry\n", file);
    fclose(file);
    return ERROR_SUCCESS;
}
BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
#else
int wmain() {
    FILE* file = nullptr;
    _wfopen_s(&file, L"chs_patch\\runtime_probe.log", L"rb");
    if (!file) return 9;
    fclose(file);
    return 0;
}
#endif
