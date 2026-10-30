#include "xp3_plain.h"
#include <cassert>
#include <cstdio>

static std::vector<uint8_t> load(const wchar_t* path) {
    FILE* file = nullptr;
    _wfopen_s(&file, path, L"rb");
    if (!file) throw std::runtime_error("Fixture is missing");
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    rewind(file);
    std::vector<uint8_t> bytes(size);
    if (fread(bytes.data(), 1, bytes.size(), file) != bytes.size()) throw std::runtime_error("Fixture read failed");
    fclose(file);
    return bytes;
}

int wmain(int argc, wchar_t** argv) {
    assert(argc == 3);
    PlainXp3 archive;
    auto bytes = load(argv[1]);
    archive.load(bytes);
    assert(archive.count() == 3);
    TVPBinaryStream* stream = archive.open(L"scn/probe.ks.scn");
    assert(stream && stream->GetSize() == 6);
    char buffer[16]{};
    assert(stream->Read(buffer, 2) == 2 && !memcmp(buffer, "ab", 2));
    assert(stream->Seek(-2, 2) == 4);
    assert(stream->Read(buffer, 16) == 2 && !memcmp(buffer, "ef", 2));
    assert(stream->Read(buffer, 1) == 0);
    assert(stream->Seek(INT64_MIN, 1) == 6);
    assert(stream->Seek(-2, 1) == 4);
    assert(stream->Seek(100, 0) == 4);
    assert(stream->Seek(0, 0) == 0);
    assert(stream->Read(buffer, 16) == 6 && !memcmp(buffer, "abcdef", 6));
    assert(stream->Write("X", 1) == 0);
    stream->SetEndOfStorage();
    assert(stream->GetSize() == 6);
    delete stream;
    stream = archive.open(L"main/中文.txt");
    assert(stream && stream->GetSize() == 3 && stream->Read(buffer, 16) == 3 && !memcmp(buffer, "xyz", 3));
    delete stream;
    stream = archive.open(L"empty.bin");
    assert(stream && stream->GetSize() == 0 && stream->Read(buffer, 1) == 0);
    delete stream;
    assert(!archive.open(L"probe.ks.scn"));
    stream = archive.open(L"SCN/PROBE.KS.SCN");
    assert(stream);
    delete stream;
    assert(resource_name(L"arc://./scn/probe.ks.scn") == L"scn/probe.ks.scn");
    assert(resource_name(L"archive://./data.xp3/scn/probe.ks.scn") == L"scn/probe.ks.scn");
    assert(resource_name(L"file://./d/game/data.xp3>scn/probe.ks.scn") == L"scn/probe.ks.scn");
    assert(resource_name(L"scn/../probe.ks.scn").empty());
    assert(resource_name(L"arc://./../probe.ks.scn").empty());
    assert(resource_name(L"scn//probe.ks.scn").empty());
    assert(resource_name(L"C:/probe.ks.scn").empty());
    for (const wchar_t* invalid : {L"scn/bad.", L"scn/bad ", L"scn/bad./entry", L"scn/bad /entry",
        L"CON", L"scn/con.txt", L"NUL.ks", L"aux", L"PRN.bin", L"COM1", L"com9.txt", L"LPT1", L"lpt9.ext",
        L"COM¹", L"COM².ext", L"COM³", L"LPT¹", L"LPT².ext", L"LPT³", L"scn/a?b", L"scn/a*b", L"scn/a|b", L"scn/a<b", L"scn/a>b", L"scn/a\"b", L"scn/a\tb"}) {
        assert(resource_name(invalid).empty());
    }
    assert(resource_name(L"scn/COM10.txt") == L"scn/COM10.txt");
    assert(resource_name(L"scn/CONSOLE.txt") == L"scn/CONSOLE.txt");
    bool failed = false;
    try { archive.load(load(argv[2])); } catch (const std::exception&) { failed = true; }
    assert(failed);
    unsigned rejected = 0;
    for (size_t size = 0; size < bytes.size(); ++size) {
        auto truncated = bytes;
        truncated.resize(size);
        try { archive.load(std::move(truncated)); } catch (const std::exception&) { ++rejected; }
    }
    assert(rejected == bytes.size());
    puts("PASS stream ABI, read/seek boundaries, Unicode paths, exact lookup, malformed/truncated XP3");
    return 0;
}
