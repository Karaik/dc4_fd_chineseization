from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

import pefile


PATTERNS = {
    "TVPCreateStream": "55 8b ec 6a ff 68 ? ? ? ? 64 a1 ? ? ? ? 50 83 ec 5c 53 56 57 a1 ? ? ? ? 33 c5 50 8d 45 f4 64 a3 ? ? ? ? 89 65 f0 89 4d ec c7 45 ? ? ? ? ? e8 ? ? ? ? 8b 4d f4 64 89 0d ? ? ? ? 59 5f 5e 5b 8b e5 5d c3",
    "TVPGetFunctionExporter": "51 80 3d ? ? ? ? 00 75 0c c6 05 ? ? ? ? 01 e8 ? ? ? ? b8 ? ? ? ? 59 c3",
}


def probe(executable: Path) -> dict:
    data = executable.read_bytes()
    pe = pefile.PE(data=data)
    matches = {}
    for name, pattern in PATTERNS.items():
        regex = b"".join(b"." if value == "?" else re.escape(bytes.fromhex(value)) for value in pattern.split())
        hits = []
        for section in pe.sections:
            if not section.Characteristics & 0x20000000:
                continue
            for match in re.finditer(regex, section.get_data(), re.DOTALL):
                rva = section.VirtualAddress + match.start()
                hits.append({"rva": rva, "va": pe.OPTIONAL_HEADER.ImageBase + rva})
        if len(hits) != 1:
            raise ValueError(f"Signature must be unique: {name}: {hits}")
        matches[name] = hits
    names = zlib.decompress(pe.get_data(0x3AF5B8, 5087)).split(b"\0")
    pointers = struct.unpack("<649I", pe.get_data(0x3AEB90, 649 * 4))
    api = {name.decode("ascii"): pointers[i] for i, name in enumerate(names[:649])}
    required = ("const tjs_char * tTJSString::c_str() const", "void ::TVPSetXP3ArchiveExtractionFilter(tTVPXP3ArchiveExtractionFilter)")
    for name in required:
        if name not in api:
            raise ValueError(f"Required API missing: {name}")
    return {
        "path": str(executable),
        "sha256": hashlib.sha256(data).hexdigest(),
        "machine": pe.FILE_HEADER.Machine,
        "image_base": pe.OPTIONAL_HEADER.ImageBase,
        "entry_rva": pe.OPTIONAL_HEADER.AddressOfEntryPoint,
        "image_size": pe.OPTIONAL_HEADER.SizeOfImage,
        "sections": [{"name": section.Name.rstrip(b"\0").decode("ascii"), "rva": section.VirtualAddress, "raw_size": section.SizeOfRawData, "entropy": round(section.get_entropy(), 3)} for section in pe.sections],
        "imports": {entry.dll.decode("ascii"): len(entry.imports) for entry in pe.DIRECTORY_ENTRY_IMPORT},
        "signatures": matches,
        "exported_plugin_apis": {name: api[name] for name in required},
        "xp3_filter_pointer_rva": 0x3B82C0,
        "xp3_native_read_rva": 0x37700,
        "static_analysis_only": True,
    }


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Verify DC4 FD runtime signatures and plugin APIs without starting the game.")
    parser.add_argument("--exe", type=Path, default=root.parent.parent / "game/DC4_FD.exe")
    parser.add_argument("--out", type=Path, default=root / "work/static_probe.json")
    args = parser.parse_args()
    result = probe(args.exe.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[done] static_probe={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
