#pragma once

// KiriKiri plugin ABI subset, adapted from tp_stub.h.
// Copyright (C) 2000-2009 W.Dee <dee@kikyou.info> and contributors.
// See THIRD_PARTY.md for the permanent upstream source and license.

#include <windows.h>

struct TVPExporter {
    virtual bool __cdecl QueryFunctions(const wchar_t**, void**, unsigned int) = 0;
    virtual bool __cdecl QueryFunctionsByNarrowString(const char**, void**, unsigned int) = 0;
};

struct TVPString {
    void* ptr;
};

static_assert(sizeof(TVPString) == 4, "This runtime requires x86.");

struct TVPImports {
    const wchar_t* (__stdcall* c_str)(const TVPString*);

    bool bind(TVPExporter* exporter) {
        const char* names[] = {
            "const tjs_char * tTJSString::c_str() const",
        };
        void* pointers[1]{};
        if (!exporter->QueryFunctionsByNarrowString(names, pointers, 1)) return false;
        for (void* pointer : pointers) if (!pointer) return false;
        c_str = reinterpret_cast<decltype(c_str)>(pointers[0]);
        return true;
    }
};

class TVPBinaryStream {
public:
    virtual unsigned __int64 __cdecl Seek(__int64 offset, int whence) = 0;
    virtual unsigned int __cdecl Read(void* buffer, unsigned int size) = 0;
    virtual unsigned int __cdecl Write(const void* buffer, unsigned int size) = 0;
    virtual void __cdecl SetEndOfStorage() = 0;
    virtual unsigned __int64 __cdecl GetSize() = 0;
    virtual ~TVPBinaryStream() = default;
};
