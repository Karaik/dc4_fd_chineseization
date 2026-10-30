from __future__ import annotations

import argparse
import csv
import io
import zipfile
import hashlib
import json
import re
import struct
from datetime import datetime
from pathlib import Path

from solution.unpack.system_inventory import EXTENSIONS, JAPANESE, bytecode_layout, decode_text
from solution.unpack.xp3 import read_index


TITLE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = TITLE_ROOT / "solution/unpack/work/data"
RAW_PATH = TITLE_ROOT / "game_script/raw_script/system/system.json"
TRANSLATED_PATH = TITLE_ROOT / "game_script/translated_script/system/system.json"
FILES = ("main/default.tjs", "main/help_opt.txt")
STRING = r'"(?:\\.|[^"\\])*"'
FIELDS = (
    ("BacklogChoiceTextFormat", r'(?m)^\s*\.BacklogChoiceTextFormat\s*=\s*(?P<value>' + STRING + ")"),
    ("soundModeUnknownText", r'(?m)^\s*\.soundModeUnknownText\s*=\s*(?P<value>' + STRING + ")"),
    ("FastSkipModeText", r'(?m)^\s*\.FastSkipModeText\s*=\s*(?P<value>' + STRING + ")"),
    ("renderSampleConfig.text1", r'(?m)^\s*text1\s*:\s*(?P<value>' + STRING + ")"),
    ("renderSampleConfig.text2", r'(?m)^\s*text2\s*:\s*(?P<value>' + STRING + ")"),
)
INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _records(path: str, text: str) -> list[dict]:
    records = []
    if path == "main/default.tjs":
        for field, pattern in FIELDS:
            matches = list(re.finditer(pattern, text))
            if len(matches) != 1:
                raise ValueError(f"Expected one system field: {field}")
            match = matches[0]
            start, end = match.span("value")
            value = json.loads(match.group("value"))
            records.append({"id": f"default:{field}", "path": path, "kind": "tjs_string", "line": text.count("\n", 0, start) + 1, "source": value, "translation": value, "start": start, "end": end})
    elif path == "main/help_opt.txt":
        cursor = 0
        key = None
        ordinal = 0
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            body = line.rstrip("\r\n")
            if body.strip() and not body.lstrip().startswith("#"):
                if not body[0].isspace():
                    key = "|".join(body.split())
                    ordinal = 0
                else:
                    if key is None:
                        raise ValueError("Help text has a body without a key")
                    ordinal += 1
                    value = body.lstrip(" \t")
                    start = cursor + len(body) - len(value)
                    records.append({"id": f"help:{key}:{ordinal}", "path": path, "kind": "help_body", "line": number, "source": value, "translation": value, "start": start, "end": cursor + len(body)})
            cursor += len(line)
    return records


def collect(source_root: Path = SOURCE_ROOT) -> tuple[dict, dict]:
    source_files = {}
    records = []
    documents = {}
    bytecode = {}
    plans = {}
    for entry in read_index(TITLE_ROOT / "game/data.xp3"):
        relative = entry.name
        if relative.startswith("$$$") or Path(relative).suffix.lower() not in EXTENSIONS:
            continue
        data = (source_root / relative).read_bytes()
        text, wrapper, encoding = decode_text(data)
        source_files[relative] = {"sha256": _hash(data), "wrapper": wrapper, "encoding": encoding}
        if encoding == "tjs2_bytecode":
            layout = bytecode_layout(data)
            strings = [
                {"index": index, "candidate": bool(JAPANESE.search(value)), "enabled": False, "source": value, "translation": value}
                for index, value in enumerate(layout["strings"])
            ]
            bytecode[relative] = {"strings": strings}
            plans[relative] = {"data": data, "layout": layout, "records": []}
        elif text is not None:
            selected = _records(relative, text)
            records.extend({key: value for key, value in row.items() if key not in ("start", "end")} for row in selected)
            documents[relative] = {"enabled": False, "review": "preserve_until_display_fields_are_confirmed", "source": text, "translation": text}
            plans[relative] = {"data": data, "text": text, "records": selected}
        else:
            raise ValueError(f"Unsupported system carrier: {relative}")
    return {
        "schema_version": 2,
        "scope": "all_original_system_carriers_with_explicit_selection",
        "source_files": source_files,
        "records": records,
        "documents": documents,
        "bytecode": bytecode,
    }, plans


def _encode_type1(text: str) -> bytes:
    data = text.encode("utf-16-le")
    return b"\xfe\xfe\x01\xff\xfe" + b"".join(
        struct.pack("<H", ((value & 0xAAAA) >> 1) | ((value & 0x5555) << 1))
        for (value,) in struct.iter_unpack("<H", data)
    )


def rewrite_bytecode(data: bytes, translations: dict[int, str]) -> bytes:
    layout = bytecode_layout(data)
    selected = {}
    for index, value in translations.items():
        if type(index) is not int or not 0 <= index < len(layout["strings"]):
            raise ValueError("Invalid TJS2 string constant index")
        _validate_value(value, f"constant:{index}")
        if value != layout["strings"][index]:
            selected[index] = value
    pieces = []
    for index, (start, end) in enumerate(layout["entries"]):
        encoded = selected.get(index, layout["strings"][index]).encode("utf-16-le")
        record = struct.pack("<I", len(encoded) // 2) + encoded
        padding = bytes((-len(record)) & 3) if index in selected else data[start + len(record) : end]
        pieces.append(record + padding)
    table = b"".join(pieces)
    payload = data[20 : layout["string_table_start"]] + table + data[layout["string_table_end"] : layout["data_end"]]
    result = bytearray(data[:12] + b"DATA" + struct.pack("<I", len(payload) + 8) + payload + data[layout["data_end"] :])
    struct.pack_into("<I", result, 8, len(result))
    result = bytes(result)
    reread = bytecode_layout(result)
    expected = [selected.get(index, value) for index, value in enumerate(layout["strings"])]
    if reread["strings"] != expected or data[layout["data_end"] :] != result[reread["data_end"] :]:
        raise ValueError("TJS2 string table or object bytes changed unexpectedly")
    if not selected and result != data:
        raise ValueError("Unchanged TJS2 reserialization is not byte-identical")
    return result


def _validate_value(value: str, identity: str) -> None:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"Invalid system translation: {identity}")
    value.encode("utf-16-le")


def _guard_matches(value, original) -> bool:
    return type(value) is type(original) and value == original


def _interpolation_guard(original: str, translated: str, identity: str) -> None:
    if INTERPOLATION.findall(translated) != INTERPOLATION.findall(original):
        raise ValueError(f"Interpolation expression changed: {identity}")
    formats = re.compile(r"%(?:[0-9]+\$)?[-+ #0]*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?(?:ll|l|h)?[diuoxXfFeEgGaAcsp%]")
    if formats.findall(translated) != formats.findall(original):
        raise ValueError(f"Format placeholders changed: {identity}")


def _encode_carrier(text: str, wrapper: str, encoding: str) -> bytes:
    if wrapper == "krkr_type1":
        return _encode_type1(text)
    encodings = {"utf-16-le-bom": (b"\xff\xfe", "utf-16-le"), "utf-16-be-bom": (b"\xfe\xff", "utf-16-be"), "utf-8-bom": (b"\xef\xbb\xbf", "utf-8"), "ascii": (b"", "ascii"), "utf-8": (b"", "utf-8"), "cp932": (b"", "cp932")}
    if wrapper != "none" or encoding not in encodings:
        raise ValueError("Unsupported system text writeback encoding")
    prefix, codec = encodings[encoding]
    return prefix + text.encode(codec)


def _ks_skeleton(text: str) -> list[str]:
    result = []
    in_script = False
    tag = re.compile(r'\[(?:[^\]"\']|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')*\]')
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        stripped = body.lstrip()
        tags = tag.findall(body)
        if in_script or stripped.startswith((";", "*", "@")) or not stripped:
            result.append(body + newline)
        else:
            pieces = tag.split(body)
            preserved = []
            for part in pieces:
                if not part:
                    preserved.append("")
                elif part.isspace():
                    preserved.append(part)
                else:
                    leading = part[:len(part)-len(part.lstrip(" \t"))]
                    trailing = "\\" if part.endswith("\\") else ""
                    preserved.append(leading + "<TEXT>" + trailing)
            result.append(json.dumps([preserved, tags, newline], ensure_ascii=False))
        if "[iscript]" in body or stripped.startswith("@iscript"):
            in_script = True
        if "[endscript]" in body or stripped.startswith("@endscript"):
            in_script = False
    return result


def _document_guard(relative: str, original: str, translated: str) -> None:
    _validate_value(translated, relative)
    _interpolation_guard(original, translated, relative)
    if relative.endswith(".ks"):
        if _ks_skeleton(original) != _ks_skeleton(translated):
            raise ValueError(f"KAG tags, script lines, labels or physical lines changed: {relative}")
    elif relative.endswith(".csv"):
        old = list(csv.reader(io.StringIO(original, newline="")))
        new = list(csv.reader(io.StringIO(translated, newline="")))
        if len(old) != len(new) or any(len(a) != len(b) or (a and (a[0] != b[0] or a[0].lstrip().startswith("#") and a != b)) for a, b in zip(old, new)):
            raise ValueError(f"CSV row keys or structure changed: {relative}")


def rebuild(document: dict, source_root: Path = SOURCE_ROOT) -> tuple[dict[str, bytes], int]:
    baseline, plans = collect(source_root)
    if not isinstance(document, dict) or set(document) != set(baseline):
        raise ValueError("Invalid system translation document")
    for field in ("schema_version", "scope", "source_files"):
        if not _guard_matches(document[field], baseline[field]):
            raise ValueError(f"Stale or modified system baseline: {field}")
    rows = document["records"]
    if not isinstance(rows, list) or len(rows) != len(baseline["records"]):
        raise ValueError("System translation record count mismatch")
    translations = {}
    for original, current in zip(baseline["records"], rows):
        if not isinstance(current, dict) or set(current) != set(original) or any(not _guard_matches(current[key], value) for key, value in original.items() if key != "translation"):
            raise ValueError(f"System source guard failed: {original['id']}")
        value = current["translation"]
        _validate_value(value, original["id"])
        _interpolation_guard(original["source"], value, original["id"])
        if current["kind"] == "help_body" and ("\n" in value or "\r" in value):
            raise ValueError(f"Help body must stay on its original physical line: {original['id']}")
        translations[current["id"]] = value
    for kind in ("documents", "bytecode"):
        if not isinstance(document[kind], dict) or document[kind].keys() != baseline[kind].keys():
            raise ValueError(f"System {kind} paths changed")
    outputs = {}
    changed = 0
    for relative, plan in plans.items():
        data = plan["data"]
        if "layout" in plan:
            base_rows = baseline["bytecode"][relative]["strings"]
            current_container = document["bytecode"][relative]
            if not isinstance(current_container, dict) or set(current_container) != {"strings"} or not isinstance(current_container["strings"], list) or len(current_container["strings"]) != len(base_rows):
                raise ValueError(f"TJS2 constant count changed: {relative}")
            edits = {}
            for original, current in zip(base_rows, current_container["strings"]):
                if not isinstance(current, dict) or set(current) != set(original) or any(not _guard_matches(current[key], value) for key, value in original.items() if key not in ("translation", "enabled")) or type(current["enabled"]) is not bool:
                    raise ValueError(f"TJS2 source/index guard failed: {relative}")
                value = current["translation"]
                _validate_value(value, relative)
                if value != original["source"]:
                    if not current["enabled"]:
                        raise ValueError(f"Explicitly enable reviewed TJS2 edits: {relative}:{original['index']}")
                    _interpolation_guard(original["source"], value, relative)
                    edits[original["index"]] = value
            outputs[relative] = rewrite_bytecode(data, edits)
            changed += len(edits)
            continue
        current = document["documents"][relative]
        original = baseline["documents"][relative]
        if not isinstance(current, dict) or set(current) != set(original) or any(not _guard_matches(current[key], value) for key, value in original.items() if key not in ("translation", "enabled")) or type(current["enabled"]) is not bool:
            raise ValueError(f"System document source guard failed: {relative}")
        value = current["translation"]
        _validate_value(value, relative)
        document_changed = value != original["source"]
        selected = plan["records"]
        replacements = []
        for row in selected:
            translation = translations[row["id"]]
            if translation != row["source"]:
                encoded = json.dumps(translation, ensure_ascii=False) if row["kind"] == "tjs_string" else translation
                replacements.append((row["start"], row["end"], encoded))
        if document_changed:
            if not current["enabled"]:
                raise ValueError(f"Explicitly enable reviewed document edits: {relative}")
            if replacements:
                raise ValueError(f"Document and field edits overlap: {relative}")
            _document_guard(relative, original["source"], value)
            result = _encode_carrier(value, baseline["source_files"][relative]["wrapper"], baseline["source_files"][relative]["encoding"])
            changed += 1
        elif replacements:
            value = plan["text"]
            for start, end, replacement in sorted(replacements, reverse=True):
                value = value[:start] + replacement + value[end:]
            result = _encode_carrier(value, baseline["source_files"][relative]["wrapper"], baseline["source_files"][relative]["encoding"])
            reread = _records(relative, decode_text(result)[0])
            if {row["id"]: row["source"] for row in reread} != {row["id"]: translations[row["id"]] for row in selected}:
                raise ValueError(f"System field re-read mismatch: {relative}")
            changed += len(replacements)
        else:
            result = data
        if decode_text(result)[0] != value and (document_changed or replacements):
            raise ValueError(f"System document re-read mismatch: {relative}")
        outputs[relative] = result
    return outputs, changed


def export(*, upgrade_source_only: bool = False) -> dict:
    document, _ = collect()
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    legacy = {
        "schema_version": 1,
        "scope": "default_five_fields_and_help_opt_bodies",
        "source_files": {name: document["source_files"][name] for name in FILES},
        "records": document["records"],
    }
    replacing = {}
    for path in (RAW_PATH, TRANSLATED_PATH):
        if not path.exists():
            continue
        existing = path.read_bytes()
        if path == RAW_PATH and existing == encoded:
            continue
        parsed = json.loads(existing)
        if path == TRANSLATED_PATH and parsed.get("schema_version") == 2:
            continue
        if not upgrade_source_only or parsed != legacy:
            raise ValueError("Existing system inputs differ; source-only schema migration requires --upgrade-source-only and exact old baseline")
        replacing[path] = existing
    backup = None
    if replacing:
        backup = TITLE_ROOT / "tmp" / ("system_schema_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".zip")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in replacing.items():
                archive.writestr(path.relative_to(TITLE_ROOT).as_posix(), data)
        with zipfile.ZipFile(backup) as archive:
            if any(archive.read(path.relative_to(TITLE_ROOT).as_posix()) != data for path, data in replacing.items()):
                raise ValueError("System schema backup verification failed")
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RAW_PATH.exists() or RAW_PATH in replacing:
        RAW_PATH.write_bytes(encoded)
    TRANSLATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TRANSLATED_PATH.exists() or TRANSLATED_PATH in replacing:
        TRANSLATED_PATH.write_bytes(encoded)
    return {"record_count": len(document["records"]), "bytecode_files": len(document["bytecode"]), "document_files": len(document["documents"]), "raw": RAW_PATH.relative_to(TITLE_ROOT).as_posix(), "translated": TRANSLATED_PATH.relative_to(TITLE_ROOT).as_posix(), "backup": backup.relative_to(TITLE_ROOT).as_posix() if backup else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export or rebuild DC4 system text with guarded explicit edit selection")
    commands = parser.add_subparsers(dest="command", required=True)
    export_parser = commands.add_parser("export", help="Create raw and editable JSON; preserve existing translations")
    export_parser.add_argument("--upgrade-source-only", action="store_true", help="Back up and migrate the exact untranslated schema-1 baseline")
    build = commands.add_parser("build", help="Rebuild wrapped system files into a new isolated work directory")
    build.add_argument("--input", type=Path, default=TRANSLATED_PATH)
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export(upgrade_source_only=args.upgrade_source_only)
        print(f"[export] {result['record_count']} display fields; {result['document_files']} editable documents; {result['bytecode_files']} bytecode string tables")
        if result["backup"]:
            print(f"[backup] {result['backup']}")
        return 0
    input_path = args.input if args.input.is_absolute() else TITLE_ROOT / args.input
    outputs, changed = rebuild(json.loads(input_path.read_text(encoding="utf-8")))
    output = TITLE_ROOT / "solution/build/work" / ("system_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(parents=True, exist_ok=False)
    selected = {name: data for name, data in outputs.items() if data != (SOURCE_ROOT / name).read_bytes()}
    for relative, data in selected.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    print(f"[build] changed_records={changed}; files={len(selected)}")
    print(f"[output] {output.relative_to(TITLE_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
