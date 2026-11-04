#include "patch.cpp"
#include <memory>

static unsigned original_calls;
static unsigned nested_depth;
static bool excessive_recursion;

static const wchar_t* __stdcall test_c_str(const TVPString* value) {
    return static_cast<const wchar_t*>(value->ptr);
}

static void* __fastcall test_original(const TVPString* name, unsigned int flags) {
    ++original_calls;
    if (++nested_depth > 4) {
        excessive_recursion = true;
        --nested_depth;
        return nullptr;
    }
    const std::wstring path(test_c_str(name));
    void* result;
    if (path == L"psb://window.pimg/648.tlg") {
        TVPString child{const_cast<wchar_t*>(L"arc://./uipsd/window.pimg")};
        result = stream_hook(&child, flags);
    } else if (path == L"psb://missing.pimg/648.tlg") {
        TVPString child{const_cast<wchar_t*>(L"arc://./uipsd/missing.pimg")};
        result = stream_hook(&child, flags);
    } else {
        static const uint8_t original_bytes[] = {'O','R','I','G'};
        result = new PlainMemoryStream(original_bytes, sizeof(original_bytes));
    }
    --nested_depth;
    return result;
}

static bool reads(const wchar_t* path, const char* expected, unsigned expected_original_calls, unsigned flags = 0) {
    original_calls = 0;
    excessive_recursion = false;
    TVPString name{const_cast<wchar_t*>(path)};
    std::unique_ptr<TVPBinaryStream> stream(static_cast<TVPBinaryStream*>(stream_hook(&name, flags)));
    if (!stream || excessive_recursion || original_calls != expected_original_calls || in_redirect) return false;
    char bytes[32]{};
    const size_t length = strlen(expected);
    return stream->GetSize() == length && stream->Read(bytes, sizeof(bytes)) == length && !memcmp(bytes, expected, length);
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) return 90;
    FILE* file = nullptr;
    _wfopen_s(&file, argv[1], L"rb");
    if (!file) return 91;
    fseek(file, 0, SEEK_END);
    const long length = ftell(file);
    fseek(file, 0, SEEK_SET);
    std::vector<uint8_t> bytes(static_cast<size_t>(length));
    const bool complete = fread(bytes.data(), 1, bytes.size(), file) == bytes.size();
    fclose(file);
    if (!complete) return 92;
    package.load(std::move(bytes));
#ifdef TEST_EXPLICIT_ALIASES
    package.enable_basename_aliases("dc5-to4-aliases-v1\nuipsd/window.pimg\n");
    basename_fallback = true;
#else
    basename_fallback = false;
#endif
    imports.c_str = test_c_str;
    InterlockedExchange(&imports_ready, 1);
    original_stream = test_original;
    if (!reads(L"psb://window.pimg/648.tlg", "PATCH", 1)) {
        fputs("FAIL nested native media reopen did not receive the canonical patch stream\n", stderr);
        return 1;
    }
    if (!reads(L"psb://missing.pimg/648.tlg", "ORIG", 2)) return 2;
    if (!reads(L"arc://./other/window.pimg", "ORIG", 1)) return 3;
    if (!reads(L"arc://./uipsd/window.pimg", "PATCH", 0)) return 4;
    if (!reads(L"arc://./uipsd/window.pimg", "ORIG", 1, 1)) return 5;
    if (!reads(L"file://./patch/chs_patch/patch.xp3>uipsd/window.pimg", "ORIG", 1)) return 6;
#ifdef TEST_EXPLICIT_ALIASES
    if (!reads(L"arc://./window.pimg", "PATCH", 0)) return 7;
#endif
    puts("nested stream fallback passed: canonical patch, missing and wrong-directory fallback, write and package exclusions");
    return 0;
}
