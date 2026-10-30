#pragma once
#include <cstdio>
#include <share.h>

inline FILE* open_log_file(const wchar_t* path) {
    return _wfsopen(path, L"wb", _SH_DENYWR);
}
