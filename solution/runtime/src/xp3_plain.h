#pragma once
#include "tvp_abi.h"
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

inline std::wstring resource_name(std::wstring path) {
    std::replace(path.begin(), path.end(), L'\\', L'/');
    if (path.rfind(L"arc://", 0) == 0) {
        path.erase(0, 6);
    } else if (path.rfind(L"archive://", 0) == 0) {
        size_t pos = path.find(L".xp3/");
        if (pos == std::wstring::npos) return {};
        path.erase(0, pos + 5);
    } else if (size_t pos = path.find(L'>'); pos != std::wstring::npos) {
        if (pos < 4 || _wcsnicmp(path.c_str() + pos - 4, L".xp3", 4) != 0) return {};
        path.erase(0, pos + 1);
    } else if (path.find(L"://") != std::wstring::npos || path.find(L':') != std::wstring::npos) {
        return {};
    }
    while (path.rfind(L"./", 0) == 0) path.erase(0, 2);
    if (path.empty() || path.front() == L'/' || path.find(L':') != std::wstring::npos || path.find(L'\0') != std::wstring::npos) return {};
    size_t pos = 0;
    do {
        size_t end = path.find(L'/', pos);
        std::wstring part = path.substr(pos, end == std::wstring::npos ? end : end - pos);
        if (part.empty() || part == L"." || part == L".." || part.back() == L'.' || part.back() == L' ') return {};
        for (wchar_t character : part) {
            if (character < 32 || wcschr(L"<>:\"|?*", character)) return {};
        }
        std::wstring stem = part.substr(0, part.find(L'.'));
        for (wchar_t& character : stem) character = towupper(character);
        if (stem == L"CON" || stem == L"PRN" || stem == L"AUX" || stem == L"NUL") return {};
        if (stem.size() == 4 && (stem.compare(0, 3, L"COM") == 0 || stem.compare(0, 3, L"LPT") == 0) &&
            ((stem[3] >= L'1' && stem[3] <= L'9') || stem[3] == L'¹' || stem[3] == L'²' || stem[3] == L'³')) return {};
        if (end == std::wstring::npos) break;
        pos = end + 1;
    } while (true);
    return path;
}

inline std::wstring resource_key(std::wstring text) {
    for (wchar_t& ch : text) ch = towlower(ch);
    return text;
}

class PlainMemoryStream final : public TVPBinaryStream {
    const uint8_t* bytes;
    uint64_t length;
    uint64_t position = 0;
public:
    PlainMemoryStream(const uint8_t* bytes, uint64_t length) : bytes(bytes), length(length) {}
    uint64_t __cdecl Seek(int64_t offset, int whence) override {
        uint64_t base = whence == 0 ? 0 : whence == 1 ? position : whence == 2 ? length : UINT64_MAX;
        if (base == UINT64_MAX) return position;
        if (offset >= 0) {
            if (static_cast<uint64_t>(offset) <= length - base) position = base + offset;
        } else {
            uint64_t distance = static_cast<uint64_t>(-(offset + 1)) + 1;
            if (distance <= base) position = base - distance;
        }
        return position;
    }
    unsigned int __cdecl Read(void* buffer, unsigned int size) override {
        size = static_cast<unsigned int>((std::min)(length - position, static_cast<uint64_t>(size)));
        if (size) memcpy(buffer, bytes + static_cast<size_t>(position), size);
        position += size;
        return size;
    }
    unsigned int __cdecl Write(const void*, unsigned int) override { return 0; }
    void __cdecl SetEndOfStorage() override {}
    uint64_t __cdecl GetSize() override { return length; }
};

class PlainXp3 {
    struct Entry { size_t offset; size_t length; };
    std::vector<uint8_t> bytes;
    std::map<std::wstring, Entry> entries;
    size_t payload_limit = 0;

    static uint64_t integer(const uint8_t* p, size_t width) {
        uint64_t result = 0;
        for (size_t i = 0; i < width; ++i) result |= static_cast<uint64_t>(p[i]) << (8 * i);
        return result;
    }
    static size_t bounded(uint64_t offset, uint64_t size, size_t limit) {
        if (offset > limit || size > limit - offset) throw std::runtime_error("XP3 range is outside its containing block");
        return static_cast<size_t>(offset + size);
    }
    void parse_file(size_t begin, size_t end) {
        std::wstring name;
        uint64_t original = UINT64_MAX, stored = UINT64_MAX;
        uint64_t segment_offset = UINT64_MAX, segment_size = UINT64_MAX;
        uint32_t checksum = 0;
        bool info_seen = false, segment_seen = false, checksum_seen = false;
        for (size_t pos = begin; pos < end;) {
            bounded(pos, 12, end);
            const uint8_t* item = bytes.data() + pos;
            size_t data = pos + 12;
            size_t next = bounded(data, integer(item + 4, 8), end);
            if (!memcmp(item, "info", 4)) {
                if (info_seen || next - data < 22) throw std::runtime_error("Invalid XP3 info block");
                info_seen = true;
                if (integer(bytes.data() + data, 4)) throw std::runtime_error("Protected XP3 entries are unsupported");
                original = integer(bytes.data() + data + 4, 8);
                stored = integer(bytes.data() + data + 12, 8);
                uint64_t count = integer(bytes.data() + data + 20, 2);
                if (count * 2 != next - data - 22) throw std::runtime_error("Invalid XP3 filename length");
                for (size_t i = 0; i < count; ++i) name.push_back(static_cast<wchar_t>(integer(bytes.data() + data + 22 + i * 2, 2)));
            } else if (!memcmp(item, "segm", 4)) {
                if (segment_seen || next - data != 28) throw std::runtime_error("Exactly one XP3 segment is required");
                segment_seen = true;
                if (integer(bytes.data() + data, 4)) throw std::runtime_error("Compressed XP3 entries are unsupported");
                segment_offset = integer(bytes.data() + data + 4, 8);
                segment_size = integer(bytes.data() + data + 12, 8);
                if (segment_size != integer(bytes.data() + data + 20, 8)) throw std::runtime_error("XP3 segment sizes differ");
            } else if (!memcmp(item, "adlr", 4)) {
                if (checksum_seen || next - data != 4) throw std::runtime_error("Invalid XP3 checksum block");
                checksum_seen = true;
                checksum = static_cast<uint32_t>(integer(bytes.data() + data, 4));
            }
            pos = next;
        }
        if (!info_seen || !segment_seen || !checksum_seen || original != stored || original != segment_size) throw std::runtime_error("XP3 entry sizes differ");
        if (segment_offset < 19) throw std::runtime_error("XP3 segment overlaps its header");
        bounded(segment_offset, segment_size, payload_limit);
        uint32_t first = 1, second = 0;
        for (size_t i = 0; i < segment_size; ++i) {
            first = (first + bytes[static_cast<size_t>(segment_offset) + i]) % 65521;
            second = (second + first) % 65521;
        }
        if ((second << 16 | first) != checksum) throw std::runtime_error("XP3 resource checksum mismatch");
        if (resource_name(name) != name) throw std::runtime_error("Invalid XP3 resource path");
        auto inserted = entries.emplace(resource_key(name), Entry{static_cast<size_t>(segment_offset), static_cast<size_t>(segment_size)});
        if (!inserted.second) throw std::runtime_error("Duplicate XP3 resource name");
    }
public:
    void load(std::vector<uint8_t> input) {
        bytes = std::move(input);
        entries.clear();
        static const uint8_t magic[] = {0x58,0x50,0x33,0x0d,0x0a,0x20,0x0a,0x1a,0x8b,0x67,0x01};
        if (bytes.size() < 28 || memcmp(bytes.data(), magic, sizeof(magic))) throw std::runtime_error("Invalid XP3 header");
        uint64_t index = integer(bytes.data() + 11, 8);
        bounded(index, 9, bytes.size());
        payload_limit = static_cast<size_t>(index);
        if (bytes[static_cast<size_t>(index)] != 0) throw std::runtime_error("Uncompressed single XP3 index is required");
        size_t begin = static_cast<size_t>(index + 9);
        size_t end = bounded(begin, integer(bytes.data() + static_cast<size_t>(index) + 1, 8), bytes.size());
        for (size_t pos = begin; pos < end;) {
            bounded(pos, 12, end);
            if (memcmp(bytes.data() + pos, "File", 4)) throw std::runtime_error("Unsupported XP3 index chunk");
            size_t next = bounded(pos + 12, integer(bytes.data() + pos + 4, 8), end);
            parse_file(pos + 12, next);
            pos = next;
        }
    }
    size_t count() const { return entries.size(); }
    TVPBinaryStream* open(const std::wstring& name) const {
        auto found = entries.find(resource_key(name));
        if (found == entries.end()) return nullptr;
        return new PlainMemoryStream(bytes.data() + found->second.offset, found->second.length);
    }
};
