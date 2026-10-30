from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import zlib
from collections import Counter
from pathlib import Path

from solution.unpack.xp3 import read_index


TITLE_ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS = {".tjs", ".ks", ".ini", ".csv", ".txt"}
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
MAX_TEXT_SIZE = 16 * 1024 * 1024


def decode_text(data: bytes) -> tuple[str | None, str, str]:
    wrapper = "none"
    if len(data) >= 5 and data[:2] == b"\xfe\xfe" and data[3:5] == b"\xff\xfe":
        mode = data[2]
        wrapper = f"krkr_type{mode}"
        payload = data[5:]
        if mode == 1:
            if len(payload) % 2:
                raise ValueError("Odd Kirikiri type-1 text size")
            data = b"\xff\xfe" + b"".join(
                struct.pack("<H", ((value & 0xAAAA) >> 1) | ((value & 0x5555) << 1))
                for (value,) in struct.iter_unpack("<H", payload)
            )
        elif mode == 2:
            if len(payload) < 16:
                raise ValueError("Truncated Kirikiri type-2 text header")
            decoder = zlib.decompressobj()
            data = decoder.decompress(payload[16:], MAX_TEXT_SIZE + 1)
            if not decoder.eof or decoder.unconsumed_tail or len(data) > MAX_TEXT_SIZE:
                raise ValueError("Invalid Kirikiri type-2 text size")
        else:
            return None, wrapper, "unsupported_text_wrapper"
    if data.startswith(b"TJS2100\0"):
        return None, wrapper, "tjs2_bytecode"
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), wrapper, "utf-16-le-bom"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), wrapper, "utf-16-be-bom"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), wrapper, "utf-8-bom"
    for encoding in ("utf-8", "cp932"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\0" not in text:
            return text, wrapper, "ascii" if text.isascii() else encoding
    return None, wrapper, "binary_or_unknown"


def bytecode_layout(data: bytes) -> dict:
    if len(data) < 24 or data[:8] != b"TJS2100\0" or data[12:16] != b"DATA":
        raise ValueError("Unsupported TJS2 header")
    if struct.unpack_from("<I", data, 8)[0] != len(data):
        raise ValueError("TJS2 file size mismatch")
    end = 12 + struct.unpack_from("<I", data, 16)[0]
    if end > len(data) or end < 24:
        raise ValueError("Invalid TJS2 DATA chunk")
    position = 20

    def count() -> int:
        nonlocal position
        if position + 4 > end:
            raise ValueError("Truncated TJS2 constant count")
        value = struct.unpack_from("<I", data, position)[0]
        position += 4
        return value

    for width in (1, 2, 4, 8, 8):
        items = count()
        position += items * width
        position = (position + 3) & ~3
        if position > end:
            raise ValueError("TJS2 numeric constants exceed DATA chunk")
    string_count = count()
    strings = []
    string_table_start = position
    entries = []
    for _ in range(string_count):
        record_start = position
        length = count() * 2
        if position + length > end:
            raise ValueError("TJS2 string exceeds DATA chunk")
        strings.append(data[position : position + length].decode("utf-16-le"))
        position = (position + length + 3) & ~3
        if position > end:
            raise ValueError("TJS2 string padding exceeds DATA chunk")
        entries.append((record_start, position))
    string_table_end = position
    octet_count = count()
    for _ in range(octet_count):
        length = count()
        position = (position + length + 3) & ~3
        if position > end:
            raise ValueError("TJS2 octet constants exceed DATA chunk")
    if position != end or data[end : end + 4] != b"OBJS" or end + 8 > len(data):
        raise ValueError("TJS2 DATA/OBJS boundary mismatch")
    if struct.unpack_from("<I", data, end + 4)[0] != len(data) - end:
        raise ValueError("TJS2 OBJS block size mismatch")
    return {
        "strings": strings,
        "entries": entries,
        "string_table_start": string_table_start,
        "string_table_end": string_table_end,
        "data_end": end,
        "octet_count": octet_count,
    }


def bytecode_strings(data: bytes) -> list[str]:
    return bytecode_layout(data)["strings"]


def inventory(source_root: Path | None = None, *, examples: int = 0) -> dict:
    source = source_root or TITLE_ROOT / "solution/unpack/work/data"
    if not source.is_dir():
        raise FileNotFoundError(f"Original data workspace not found: {source}")
    records = []
    missing = []
    failures = []
    for entry in read_index(TITLE_ROOT / "game/data.xp3"):
        if entry.name.startswith("$$$") or Path(entry.name).suffix.lower() not in EXTENSIONS:
            continue
        path = source / entry.name
        if not path.is_file():
            missing.append(entry.name)
            continue
        data = path.read_bytes()
        if len(data) > MAX_TEXT_SIZE:
            failures.append({"path": entry.name, "error": "File exceeds inventory size limit"})
            continue
        try:
            text, wrapper, encoding = decode_text(data)
            record = {
                "path": entry.name,
                "extension": path.suffix.lower(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "wrapper": wrapper,
                "encoding": encoding,
            }
            if text is not None:
                lines = text.splitlines()
                record["line_count"] = len(lines)
                record["japanese_line_count"] = sum(bool(JAPANESE.search(line)) for line in lines)
                if examples:
                    record["examples"] = [
                        {"line": number, "text": line[:180]}
                        for number, line in enumerate(lines, 1) if JAPANESE.search(line)
                    ][:examples]
            elif encoding == "tjs2_bytecode":
                strings = bytecode_strings(data)
                candidates = [(index, value) for index, value in enumerate(strings) if JAPANESE.search(value)]
                record["constant_string_count"] = len(strings)
                record["japanese_constant_count"] = len(candidates)
                if examples:
                    record["examples"] = [{"constant_index": index, "text": value[:180]} for index, value in candidates[:examples]]
            records.append(record)
        except (ValueError, UnicodeDecodeError, struct.error, zlib.error) as error:
            failures.append({"path": entry.name, "error": str(error)})
    summary = Counter((row["extension"], row["wrapper"], row["encoding"]) for row in records)
    return {
        "schema_version": 1,
        "project": "dc4_fd_chineseization",
        "input": source.relative_to(TITLE_ROOT).as_posix(),
        "selection": "Original data.xp3 index names with .tjs/.ks/.ini/.csv/.txt; generated files excluded",
        "file_count": len(records),
        "summary": [{"extension": key[0], "wrapper": key[1], "encoding": key[2], "count": value} for key, value in sorted(summary.items())],
        "missing": missing,
        "failures": failures,
        "limits": [
            "日文行数和字节码常量数仅为定位依据，包含注释、资源标识和逻辑字符串",
            "TJS2 只读取 DATA 字符串常量表，不反编译指令、不修改字节码",
            "载体编码盘点不等于完成显示文本导出或翻译",
        ],
        "files": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory original system text carriers without modifying extracted resources")
    parser.add_argument("--examples", type=int, default=0, choices=range(0, 6), help="Optional bounded Japanese examples per file (default: counts only)")
    args = parser.parse_args(argv)
    report = inventory(examples=args.examples)
    destination = TITLE_ROOT / "solution/unpack/work/system_inventory.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[inventory] {report['file_count']} files; missing={len(report['missing'])}; failures={len(report['failures'])}")
    print(f"[report] {destination.relative_to(TITLE_ROOT).as_posix()}")
    return int(bool(report["missing"] or report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
