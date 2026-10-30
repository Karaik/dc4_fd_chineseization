#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <cwctype>
#include <string>
#include <vector>
#include <algorithm>
#include "tvp_abi.h"
#include "xp3_plain.h"
#include "log_file.h"

#define MINHOOK_IMPLEMENTATION
#define MINHOOK_STATIC
#pragma warning(push, 0)
#include "compat/stb_minhook_v1332.h"
#pragma warning(pop)

using StreamCreate = void* (__fastcall*)(const TVPString*, unsigned int);
using ExporterGet = TVPExporter* (__cdecl*)();
static StreamCreate original_stream;
static ExporterGet original_exporter;
static decltype(&CreateFontIndirectW) original_font;
static decltype(&EnumFontFamiliesExW) original_enum;
static TVPImports imports{};
static volatile LONG imports_ready;
static volatile LONG started;
static FILE* logfile;
static SRWLOCK log_lock = SRWLOCK_INIT;
static std::wstring root_dir;
static std::wstring package_file;
static PlainXp3 package;
static std::wstring font_name;
static std::wstring font_file;
static int charset;
static int loglevel;
static bool basename_fallback;
static thread_local bool in_redirect;
static SRWLOCK import_lock = SRWLOCK_INIT;

static std::string utf8(const std::wstring& text) {
    int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    if (size) WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
    return result;
}

static void log(const char* format, ...) {
    if (!logfile) return;
    AcquireSRWLockExclusive(&log_lock);
    va_list args;
    va_start(args, format);
    vfprintf(logfile, format, args);
    va_end(args);
    fputc('\n', logfile);
    fflush(logfile);
    ReleaseSRWLockExclusive(&log_lock);
}

static std::wstring module_path() {
    std::vector<wchar_t> path(512);
    for (;;) {
        DWORD size = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
        if (!size) return {};
        if (size < path.size() - 1) return std::wstring(path.data(), size);
        path.resize(path.size() * 2);
    }
}

static std::wstring setting(const wchar_t* key, const wchar_t* fallback) {
    wchar_t text[1024]{};
    GetPrivateProfileStringW(L"runtime", key, fallback, text, 1024, (root_dir + L"\\chs_patch\\config.ini").c_str());
    return text;
}

static void* unique_signature(HMODULE module, const char* pattern) {
    std::vector<int> signature;
    for (const char* p = pattern; *p;) {
        if (*p == ' ') { ++p; continue; }
        if (*p == '?') { signature.push_back(-1); ++p; continue; }
        char* next;
        signature.push_back(static_cast<int>(strtoul(p, &next, 16)));
        p = next;
    }
    auto base = reinterpret_cast<unsigned char*>(module);
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS32*>(base + dos->e_lfanew);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386) return nullptr;
    void* result = nullptr;
    unsigned count = 0;
    auto section = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++section) {
        if (!(section->Characteristics & IMAGE_SCN_MEM_EXECUTE)) continue;
        auto bytes = base + section->VirtualAddress;
        size_t size = section->Misc.VirtualSize;
        for (size_t pos = 0; pos + signature.size() <= size; ++pos) {
            bool match = true;
            for (size_t j = 0; j < signature.size(); ++j) {
                if (signature[j] >= 0 && signature[j] != bytes[pos + j]) { match = false; break; }
            }
            if (match) { result = bytes + pos; ++count; }
        }
    }
    return count == 1 ? result : nullptr;
}

static bool bind_hook(void* target, void* replacement, void** original, const char* name) {
    MH_STATUS status = MH_CreateHook(target, replacement, original);
    log("HOOK %s address=%p status=%s", name, target, MH_StatusToString(status));
    return status == MH_OK;
}

static TVPExporter* __cdecl exporter_hook() {
    TVPExporter* exporter = original_exporter();
    if (!InterlockedCompareExchange(&imports_ready, 0, 0)) {
        AcquireSRWLockExclusive(&import_lock);
        if (imports.bind(exporter)) {
            InterlockedExchange(&imports_ready, 1);
            log("IMPORTS ready exporter=%p", exporter);
        } else {
            log("ERROR required KiriKiri imports are unavailable");
        }
        ReleaseSRWLockExclusive(&import_lock);
    }
    return exporter;
}

static void* __fastcall stream_hook(const TVPString* name, unsigned int flags) {
    if (in_redirect || (flags & 15) != 0 || !InterlockedCompareExchange(&imports_ready, 0, 0)) {
        return original_stream(name, flags);
    }
    struct Scope { Scope() { in_redirect = true; } ~Scope() { in_redirect = false; } } scope;
    std::wstring input(imports.c_str(name));
    if (input.find(L"chs_patch/patch.xp3>") != std::wstring::npos) return original_stream(name, flags);
    std::wstring resource = resource_name(input);
    if (resource.empty()) return original_stream(name, flags);
    for (int attempt = 0; attempt < (basename_fallback ? 2 : 1); ++attempt) {
        if (attempt) {
            size_t pos = resource.find_last_of(L'/');
            if (pos == std::wstring::npos) break;
            resource.erase(0, pos + 1);
        }
        TVPBinaryStream* candidate = package.open(resource);
        if (!candidate) continue;
        log("%s %s -> %s", attempt ? "REDIRECT_BASENAME" : "REDIRECT", utf8(input).c_str(), utf8(resource).c_str());
        return candidate;
    }
    if (loglevel >= 2) log("MISS %s", utf8(input).c_str());
    return original_stream(name, flags);
}

static void apply_font(LOGFONTW& font) {
    if (charset) font.lfCharSet = static_cast<BYTE>(charset);
    if (!font_name.empty()) {
        std::wstring chosen = font_name;
        if (font.lfFaceName[0] == L'@' && chosen[0] != L'@') chosen.insert(0, 1, L'@');
        wcsncpy_s(font.lfFaceName, chosen.c_str(), _TRUNCATE);
    }
}

static HFONT WINAPI font_hook(const LOGFONTW* requested) {
    LOGFONTW font = *requested;
    apply_font(font);
    static volatile LONG calls;
    if (InterlockedIncrement(&calls) <= 50) log("FONT %s -> %s charset=%u", utf8(requested->lfFaceName).c_str(), utf8(font.lfFaceName).c_str(), font.lfCharSet);
    return original_font(&font);
}

static int WINAPI enum_hook(HDC dc, LPLOGFONTW requested, FONTENUMPROCW callback, LPARAM parameter, DWORD flags) {
    LOGFONTW font = *requested;
    if (charset) font.lfCharSet = static_cast<BYTE>(charset);
    return original_enum(dc, &font, callback, parameter, flags);
}

extern "C" DWORD WINAPI StartRuntime(void*) {
    if (InterlockedCompareExchange(&started, 1, 0)) return ERROR_ALREADY_INITIALIZED;
    std::wstring executable = module_path();
    size_t slash = executable.find_last_of(L"\\/");
    if (slash == std::wstring::npos || _wcsicmp(executable.c_str() + slash + 1, L"DC4_FD.exe")) return ERROR_BAD_EXE_FORMAT;
    root_dir = executable.substr(0, slash);
    package_file = root_dir + L"\\chs_patch\\patch.xp3";
    if (GetFileAttributesW(package_file.c_str()) == INVALID_FILE_ATTRIBUTES) return ERROR_FILE_NOT_FOUND;
    logfile = open_log_file((root_dir + L"\\chs_patch\\runtime.log").c_str());
    if (!logfile) return ERROR_ACCESS_DENIED;
    log("DC4 FD localization runtime 1");
    font_name = setting(L"fontname", L"Microsoft YaHei");
    font_file = setting(L"fontpath", L"");
    charset = _wtoi(setting(L"charset", L"134").c_str());
    loglevel = _wtoi(setting(L"loglevel", L"1").c_str());
    basename_fallback = setting(L"basenamefallback", L"0") == L"1";
    FILE* archive = nullptr;
    _wfopen_s(&archive, package_file.c_str(), L"rb");
    if (!archive) return ERROR_FILE_NOT_FOUND;
    _fseeki64(archive, 0, SEEK_END);
    __int64 size = _ftelli64(archive);
    _fseeki64(archive, 0, SEEK_SET);
    if (size < 0 || size > 512 * 1024 * 1024) { fclose(archive); log("ERROR patch XP3 exceeds 512 MiB"); return ERROR_BAD_LENGTH; }
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    bool read = fread(bytes.data(), 1, bytes.size(), archive) == bytes.size();
    fclose(archive);
    if (!read) return ERROR_READ_FAULT;
    try { package.load(std::move(bytes)); }
    catch (const std::exception& error) { log("ERROR %s", error.what()); return ERROR_INVALID_DATA; }
    log("PACKAGE entries=%zu native_filter_bypassed=1", package.count());
    HMODULE executable_module = GetModuleHandleW(nullptr);
    void* stream = unique_signature(executable_module,
        "55 8b ec 6a ff 68 ? ? ? ? 64 a1 ? ? ? ? 50 83 ec 5c 53 56 57 a1 ? ? ? ? 33 c5 50 8d 45 f4 64 a3 ? ? ? ? 89 65 f0 89 4d ec c7 45 ? ? ? ? ? e8 ? ? ? ? 8b 4d f4 64 89 0d ? ? ? ? 59 5f 5e 5b 8b e5 5d c3");
    void* exporter = unique_signature(executable_module,
        "51 80 3d ? ? ? ? 00 75 0c c6 05 ? ? ? ? 01 e8 ? ? ? ? b8 ? ? ? ? 59 c3");
    log("TARGET stream_rva=%x exporter_rva=%x", stream ? static_cast<unsigned>(reinterpret_cast<uintptr_t>(stream) - reinterpret_cast<uintptr_t>(executable_module)) : 0,
        exporter ? static_cast<unsigned>(reinterpret_cast<uintptr_t>(exporter) - reinterpret_cast<uintptr_t>(executable_module)) : 0);
    if (!stream || !exporter) { log("ERROR target signatures must each match exactly once"); return ERROR_REVISION_MISMATCH; }
    if (MH_Initialize() != MH_OK) return ERROR_DLL_INIT_FAILED;
    bool success = bind_hook(stream, reinterpret_cast<void*>(stream_hook), reinterpret_cast<void**>(&original_stream), "TVPCreateStream") &&
        bind_hook(exporter, reinterpret_cast<void*>(exporter_hook), reinterpret_cast<void**>(&original_exporter), "TVPGetFunctionExporter");
    if (!font_name.empty() || charset) {
        success = success && bind_hook(reinterpret_cast<void*>(CreateFontIndirectW), reinterpret_cast<void*>(font_hook), reinterpret_cast<void**>(&original_font), "CreateFontIndirectW");
        success = success && bind_hook(reinterpret_cast<void*>(EnumFontFamiliesExW), reinterpret_cast<void*>(enum_hook), reinterpret_cast<void**>(&original_enum), "EnumFontFamiliesExW");
    }
    if (!font_file.empty()) {
        if (font_file.find(L':') != std::wstring::npos || font_file.find(L"..") != std::wstring::npos || font_file.front() == L'/' || font_file.front() == L'\\') {
            log("ERROR fontpath must be relative to the package directory"); return ERROR_BAD_PATHNAME;
        }
        int count = AddFontResourceExW((root_dir + L"\\" + font_file).c_str(), FR_PRIVATE, nullptr);
        log("FONTFILE %s count=%d", utf8(font_file).c_str(), count);
        if (!count) return ERROR_FILE_NOT_FOUND;
    }
    if (!success || MH_EnableHook(MH_ALL_HOOKS) != MH_OK) { log("ERROR hook setup failed"); return ERROR_DLL_INIT_FAILED; }
    log("READY package=%s basename_fallback=%u codepage_override=0", utf8(package_file).c_str(), basename_fallback);
    return ERROR_SUCCESS;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(instance);
    return TRUE;
}
