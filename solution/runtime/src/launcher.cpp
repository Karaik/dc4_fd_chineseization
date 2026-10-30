#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>
#include <set>
#include "log_file.h"

static FILE* logfile;
static bool no_dialog;

static std::wstring module_path(HMODULE module = nullptr) {
    std::vector<wchar_t> path(512);
    for (;;) {
        DWORD size = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
        if (!size) return {};
        if (size < path.size() - 1) return std::wstring(path.data(), size);
        path.resize(path.size() * 2);
    }
}

static std::wstring quote(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') { ++slashes; continue; }
        result.append(ch == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        result.push_back(ch);
        slashes = 0;
    }
    result.append(slashes * 2, L'\\');
    return result + L'"';
}

static int fail(const wchar_t* message, DWORD code) {
    if (logfile) { fprintf(logfile, "ERROR %ls code=%lu\n", message, code); fflush(logfile); }
    if (!no_dialog) {
        std::wstring text = std::wstring(message) + L"\nError code: " + std::to_wstring(code);
        MessageBoxW(nullptr, text.c_str(), L"DC4 FD localization launcher", MB_OK | MB_ICONERROR);
    }
    return static_cast<int>(code ? code : 1);
}

static bool file_exists(const std::wstring& path) {
    DWORD attr = GetFileAttributesW(path.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static uintptr_t remote_module(DWORD pid, const std::wstring& name) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    uintptr_t address = 0;
    if (Module32FirstW(snapshot, &entry)) {
        do {
            if (!_wcsicmp(entry.szModule, name.c_str())) { address = reinterpret_cast<uintptr_t>(entry.modBaseAddr); break; }
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return address;
}

static bool remote_read(HANDLE process, uintptr_t address, void* buffer, SIZE_T size) {
    SIZE_T count = 0;
    return ReadProcessMemory(process, reinterpret_cast<const void*>(address), buffer, size, &count) && count == size;
}

static uintptr_t mapped_export(HANDLE process, const char* module_name, const char* export_name) {
    std::set<uintptr_t> visited;
    for (uintptr_t address = 0x10000; address < 0x80000000;) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQueryEx(process, reinterpret_cast<const void*>(address), &region, sizeof(region))) break;
        uintptr_t next = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (next <= address) break;
        address = next;
        uintptr_t base = reinterpret_cast<uintptr_t>(region.AllocationBase);
        if (region.Type != MEM_IMAGE || !visited.insert(base).second) continue;
        IMAGE_DOS_HEADER dos{};
        IMAGE_NT_HEADERS32 nt{};
        if (!remote_read(process, base, &dos, sizeof(dos)) || dos.e_magic != IMAGE_DOS_SIGNATURE ||
            dos.e_lfanew <= 0 || dos.e_lfanew > 1024 * 1024 ||
            !remote_read(process, base + dos.e_lfanew, &nt, sizeof(nt)) || nt.Signature != IMAGE_NT_SIGNATURE ||
            nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC) continue;
        const auto directory = nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
        IMAGE_EXPORT_DIRECTORY exports{};
        if (!directory.VirtualAddress || directory.VirtualAddress >= nt.OptionalHeader.SizeOfImage ||
            !remote_read(process, base + directory.VirtualAddress, &exports, sizeof(exports))) continue;
        char name[128]{};
        if (!remote_read(process, base + exports.Name, name, sizeof(name) - 1) || _stricmp(name, module_name)) continue;
        if (exports.NumberOfNames > 65536 || exports.NumberOfFunctions > 65536) return 0;
        std::vector<DWORD> names(exports.NumberOfNames), functions(exports.NumberOfFunctions);
        std::vector<WORD> ordinals(exports.NumberOfNames);
        if (!remote_read(process, base + exports.AddressOfNames, names.data(), names.size() * sizeof(DWORD)) ||
            !remote_read(process, base + exports.AddressOfNameOrdinals, ordinals.data(), ordinals.size() * sizeof(WORD)) ||
            !remote_read(process, base + exports.AddressOfFunctions, functions.data(), functions.size() * sizeof(DWORD))) return 0;
        for (size_t index = 0; index < names.size(); ++index) {
            char symbol[128]{};
            if (!remote_read(process, base + names[index], symbol, sizeof(symbol) - 1) || strcmp(symbol, export_name)) continue;
            if (ordinals[index] >= functions.size()) return 0;
            DWORD rva = functions[ordinals[index]];
            if (rva >= nt.OptionalHeader.SizeOfImage ||
                (rva >= directory.VirtualAddress && rva - directory.VirtualAddress < directory.Size)) return 0;
            return base + rva;
        }
        return 0;
    }
    return 0;
}

static uintptr_t remote_system_function(DWORD pid, FARPROC local) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(local), &module)) return 0;
    std::wstring path = module_path(module);
    size_t slash = path.find_last_of(L"\\/");
    uintptr_t base = remote_module(pid, path.substr(slash + 1));
    return base ? base + reinterpret_cast<uintptr_t>(local) - reinterpret_cast<uintptr_t>(module) : 0;
}

static bool call_remote(HANDLE process, uintptr_t address, void* argument, DWORD& result) {
    HANDLE thread = CreateRemoteThread(process, nullptr, 0, reinterpret_cast<LPTHREAD_START_ROUTINE>(address), argument, 0, nullptr);
    if (!thread) return false;
    bool success = WaitForSingleObject(thread, 30000) == WAIT_OBJECT_0 && GetExitCodeThread(thread, &result);
    CloseHandle(thread);
    return success;
}

static DWORD inject(PROCESS_INFORMATION& process, const std::wstring& dll_path, uintptr_t start_rva) {
    SIZE_T size = (dll_path.size() + 1) * sizeof(wchar_t);
    void* remote_path = VirtualAllocEx(process.hProcess, nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote_path) return GetLastError();
    SIZE_T written = 0;
    if (!WriteProcessMemory(process.hProcess, remote_path, dll_path.c_str(), size, &written) || written != size) return ERROR_WRITE_FAULT;
    auto load = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    uintptr_t load_remote = remote_system_function(process.dwProcessId, load);
    if (!load_remote) {
        uintptr_t exit_thread = mapped_export(process.hProcess, "ntdll.dll", "RtlExitUserThread");
        fprintf(logfile, "INJECT loader_bootstrap=%p main_thread_suspended=1\n", reinterpret_cast<void*>(exit_thread));
        fflush(logfile);
        DWORD exit_code = ERROR_DLL_INIT_FAILED;
        if (!exit_thread || !call_remote(process.hProcess, exit_thread, nullptr, exit_code) || exit_code != 0) return ERROR_DLL_INIT_FAILED;
        load_remote = remote_system_function(process.dwProcessId, load);
    }
    fprintf(logfile, "INJECT LoadLibraryW=%p StartRuntime_rva=%zx\n", reinterpret_cast<void*>(load_remote), start_rva);
    fflush(logfile);
    DWORD dll_base = 0;
    if (!load_remote || !call_remote(process.hProcess, load_remote, remote_path, dll_base)) return ERROR_DLL_INIT_FAILED;
    VirtualFreeEx(process.hProcess, remote_path, 0, MEM_RELEASE);
    if (!dll_base) return ERROR_DLL_NOT_FOUND;
    fprintf(logfile, "INJECT module=%08lx\n", dll_base);
    fflush(logfile);
    DWORD result = ERROR_DLL_INIT_FAILED;
    if (!call_remote(process.hProcess, dll_base + start_rva, nullptr, result)) return ERROR_DLL_INIT_FAILED;
    return result;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 1;
    bool check_only = false;
    bool wait = false;
    std::wstring extra;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--check")) check_only = true;
        else if (!wcscmp(argv[i], L"--no-dialog")) no_dialog = true;
        else if (!wcscmp(argv[i], L"--wait")) wait = true;
        else if (!_wcsnicmp(argv[i], L"-datapath", 9)) { LocalFree(argv); return fail(L"The save directory is controlled by this launcher.", ERROR_INVALID_PARAMETER); }
        else extra += L" " + quote(argv[i]);
    }
    LocalFree(argv);
    std::wstring self = module_path();
    size_t slash = self.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return fail(L"Cannot locate the launcher directory.", ERROR_BAD_PATHNAME);
    std::wstring root = self.substr(0, slash);
    logfile = open_log_file((root + L"\\chs_patch\\launcher.log").c_str());
    std::wstring executable = root + L"\\DC4_FD.exe";
    std::wstring dll = root + L"\\dc4_fd_chs.dll";
    if (!file_exists(executable)) return fail(L"DC4_FD.exe is missing.", ERROR_FILE_NOT_FOUND);
    if (!file_exists(dll)) return fail(L"dc4_fd_chs.dll is missing.", ERROR_FILE_NOT_FOUND);
    if (!file_exists(root + L"\\chs_patch\\patch.xp3")) return fail(L"chs_patch/patch.xp3 is missing.", ERROR_FILE_NOT_FOUND);
    if (!file_exists(root + L"\\chs_patch\\config.ini")) return fail(L"chs_patch/config.ini is missing.", ERROR_FILE_NOT_FOUND);
    if (!logfile) return fail(L"Cannot write chs_patch/launcher.log.", ERROR_ACCESS_DENIED);
    HMODULE local_dll = LoadLibraryExW(dll.c_str(), nullptr, DONT_RESOLVE_DLL_REFERENCES);
    if (!local_dll) return fail(L"Cannot inspect dc4_fd_chs.dll.", GetLastError());
    auto start = GetProcAddress(local_dll, "StartRuntime");
    uintptr_t start_rva = reinterpret_cast<uintptr_t>(start) - reinterpret_cast<uintptr_t>(local_dll);
    FreeLibrary(local_dll);
    if (!start) return fail(L"StartRuntime is missing from dc4_fd_chs.dll.", ERROR_PROC_NOT_FOUND);
    if (check_only) { fputs("CHECK passed; game not started\n", logfile); fclose(logfile); return 0; }
    std::wstring command = quote(executable) + L" " + quote(L"-datapath=$(exepath)\\savedata_chs") + extra;
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(executable.c_str(), mutable_command.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED,
        nullptr, root.c_str(), &startup, &process)) return fail(L"Cannot start DC4_FD.exe.", GetLastError());
    fprintf(logfile, "PROCESS pid=%lu save_directory=savedata_chs\n", process.dwProcessId);
    fflush(logfile);
    DWORD error = inject(process, dll, start_rva);
    if (error || ResumeThread(process.hThread) == static_cast<DWORD>(-1)) {
        if (!error) error = GetLastError();
        TerminateProcess(process.hProcess, error);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return fail(L"Runtime initialization failed; game startup was stopped. See chs_patch/runtime.log.", error);
    }
    fputs("READY runtime initialized; game resumed\n", logfile);
    fclose(logfile);
    CloseHandle(process.hThread);
    DWORD exit_code = 0;
    if (wait) {
        WaitForSingleObject(process.hProcess, INFINITE);
        GetExitCodeProcess(process.hProcess, &exit_code);
    }
    CloseHandle(process.hProcess);
    return static_cast<int>(exit_code);
}
