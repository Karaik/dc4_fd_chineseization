from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path


TITLE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXE_SHA256 = "db175d1d01c81822fd099d4a435c1bc2d3bdf0486e2738002d1241f592361a8e"
SOURCE_MODULE_SHA256 = "c28025c7452b63e22b2841c4150a9965357196d3b23ad8e3b58a9d607c6827f4"
MAX_MODULE_SIZE = 2 * 1024 * 1024


def _module_from_executable(executable: bytes) -> tuple[bytes, int, int]:
    matches = []
    position = 0
    while True:
        position = executable.find(b"\x78", position)
        if position < 0:
            break
        start = position
        position += 1
        if position >= len(executable) or executable[position] not in (1, 0x5E, 0x9C, 0xDA):
            continue
        try:
            decoder = zlib.decompressobj()
            packed = executable[start : start + MAX_MODULE_SIZE]
            module = decoder.decompress(packed, MAX_MODULE_SIZE)
        except zlib.error:
            continue
        if not decoder.eof or not module.startswith(b"MZ"):
            continue
        if hashlib.sha256(module).hexdigest() != SOURCE_MODULE_SHA256:
            continue
        if b"cxdec.tpm.dll" not in module or b"TVPSetXP3ArchiveExtractionFilter" not in module:
            continue
        matches.append((module, start, len(packed) - len(decoder.unused_data)))
    if len(matches) != 1:
        raise ValueError(f"Expected one verified embedded Cx module, found {len(matches)}")
    return matches[0]


def _records(module: bytes) -> tuple[dict[str, tuple[int, bytes]], int]:
    candidates = []
    position = 0
    while True:
        position = module.find(b"PARAMS\0", position)
        if position < 0:
            break
        start = position
        position += 1
        cursor = start
        records = {}
        try:
            while cursor < len(module) and module[cursor]:
                end = module.index(0, cursor)
                label = module[cursor:end].decode("ascii")
                size = struct.unpack_from("<H", module, end + 1)[0]
                body = end + 3
                if not label or label in records or not size or body + size > len(module):
                    raise ValueError("Invalid Cx record")
                records[label] = (body, module[body : body + size])
                cursor = body + size
            if len(records["PARAMS"][1]) == 22 and len(records["TABLE"][1]) == 4096:
                candidates.append((records, start))
        except (ValueError, UnicodeDecodeError, struct.error, KeyError):
            continue
    if len(candidates) != 1:
        raise ValueError(f"Expected one Cx PARAMS/TABLE record set, found {len(candidates)}")
    return candidates[0]


def _order(encoded: bytes, operations: list[int]) -> list[int]:
    if sorted(encoded) != list(range(len(operations))):
        raise ValueError("Invalid native Cx branch permutation")
    result = [0] * len(operations)
    for selector, operation in zip(encoded, operations):
        result[selector] = operation
    return result


def extract_key(executable: bytes) -> tuple[dict, bytes]:
    executable_hash = hashlib.sha256(executable).hexdigest()
    if executable_hash != SOURCE_EXE_SHA256:
        raise ValueError("Unverified game executable; this extractor supports the recorded DC4 baseline only")
    module, module_offset, packed_size = _module_from_executable(executable)
    records, records_offset = _records(module)
    params_offset, params = records["PARAMS"]
    table_offset, table = records["TABLE"]
    if params[17] != 0:
        raise ValueError("Unsupported Cx table or random-generator flags")
    mask, offset = struct.unpack_from("<HH", params, 18)
    config = {
        "schema_version": 1,
        "project": "dc4_fd_chineseization",
        "algorithm": "cxdec-v1",
        "random_generator": "lcg",
        "mask": mask,
        "offset": offset,
        "prolog_order": _order(params[14:17], [0, 1, 2]),
        "odd_branch_order": _order(params[8:14], [2, 5, 3, 4, 1, 0]),
        "even_branch_order": _order(params[:8], [0, 2, 3, 1, 5, 6, 7, 4]),
        "control_block": "control_block.bin",
        "control_block_sha256": hashlib.sha256(table).hexdigest(),
        "startup_tjs_not_encrypted": False,
        "source": {
            "executable": "game/DC4_FD.exe",
            "executable_sha256": executable_hash,
            "compressed_module_file_offset": module_offset,
            "compressed_module_bytes": packed_size,
            "module_bytes": len(module),
            "module_sha256": hashlib.sha256(module).hexdigest(),
            "records_file_offset": records_offset,
            "params_file_offset": params_offset,
            "params_hex": params.hex(),
            "table_file_offset": table_offset,
        },
    }
    return config, table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract DC4 Cx parameters from the project-local original executable")
    parser.add_argument("--check", action="store_true", help="Compare the tracked key files with a fresh extraction without writing")
    args = parser.parse_args(argv)
    config, control = extract_key((TITLE_ROOT / "game/DC4_FD.exe").read_bytes())
    outputs = {
        "key.json": (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "control_block.bin": control,
    }
    output_root = Path(__file__).resolve().parent
    for name, expected in outputs.items():
        path = output_root / name
        if args.check:
            if path.read_bytes() != expected:
                raise ValueError(f"Key material differs from original executable: {name}")
            print(f"[check] {path.relative_to(TITLE_ROOT).as_posix()}")
        else:
            path.write_bytes(expected)
            print(f"[write] {path.relative_to(TITLE_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
