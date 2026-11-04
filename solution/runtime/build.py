from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import struct
import subprocess
import shutil
import uuid
import zlib


ROOT = Path(__file__).resolve().parent
TITLE = ROOT.parent.parent


def _no_reparse(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise ValueError(f'Runtime output contains a link or reparse point: {path}')
    return info


def validate_output(path: Path) -> Path:
    raw = Path(path)
    devices = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$', re.IGNORECASE)
    if '..' in raw.parts or any(part.endswith(('.', ' ')) or devices.fullmatch(part) or any(ord(char) < 32 or char in '<>:"|?*' for char in part) for part in raw.parts if part != raw.anchor):
        raise ValueError(f'Runtime output contains an invalid path component: {path}')
    absolute = raw.absolute()
    roots = (TITLE / 'out', ROOT / 'out', ROOT / 'work')
    if not any(absolute == root or absolute.is_relative_to(root) for root in roots):
        raise ValueError(f'Runtime output must be inside project out/, solution/runtime/out/ or solution/runtime/work/: {path}')
    for node in (*reversed(absolute.parents), absolute):
        if os.path.lexists(node):
            _no_reparse(node)
    if absolute.resolve() != absolute:
        raise ValueError(f'Runtime output path resolution changed: {path}')
    if absolute.exists():
        if not absolute.is_dir():
            raise ValueError(f'Runtime output must be a directory: {path}')
        pending = [absolute]
        while pending:
            for child in pending.pop().iterdir():
                info = _no_reparse(child)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(child)
    return absolute


def vcvarsall() -> Path:
    installer = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    candidates = []
    if installer.is_file():
        result = subprocess.run([str(installer), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"], capture_output=True, text=True, check=True)
        if result.stdout.strip():
            candidates.append(Path(result.stdout.strip()) / "VC/Auxiliary/Build/vcvarsall.bat")
    base = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio"
    candidates.extend(base / version / edition / "VC/Auxiliary/Build/vcvarsall.bat" for version in ("2022", "2019") for edition in ("BuildTools", "Community", "Professional", "Enterprise"))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("MSVC x86 build tools were not found")


def compile_source(out: Path, name: str, source: str, extra: list[str], setup: Path) -> Path:
    arguments = ["/nologo", "/utf-8", "/std:c++20", "/EHsc", "/W4", "/O2", "/MT", "/DWIN32", "/DNOMINMAX", str(ROOT / "src" / source), *extra]
    response = out / f"{name}.rsp"
    response.write_text(" ".join('"' + arg.replace('"', '\\"') + '"' for arg in arguments), encoding="utf-8-sig")
    script = out / f"{name}.cmd"
    script.write_text(f'@echo off\ncall "{setup}" x86 >nul\nif errorlevel 1 exit /b %errorlevel%\ncl @{response.name}\n', encoding="utf-8")
    subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=out, check=True)
    return out / name


def pe_summary(path: Path) -> dict:
    import pefile

    pe = pefile.PE(str(path))
    if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.Magic != 0x10B:
        raise ValueError(f"Expected x86 PE32: {path}")
    exports = []
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        exports = [symbol.name.decode("ascii") for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name]
    return {"path": str(path), "machine": "x86", "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "exports": exports}


def chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<Q", len(payload)) + payload


def fixture() -> bytes:
    data = bytearray(b"XP3\r\n \n\x1a\x8bg\x01" + b"\0" * 8)
    index = bytearray()
    for name, payload in (("scn/probe.ks.scn", b"abcdef"), ("main/中文.txt", b"xyz"), ("empty.bin", b"")):
        offset = len(data)
        data.extend(payload)
        encoded = name.encode("utf-16le")
        info = chunk(b"info", struct.pack("<IQQH", 0, len(payload), len(payload), len(encoded) // 2) + encoded)
        segment = chunk(b"segm", struct.pack("<IQQQ", 0, offset, len(payload), len(payload)))
        index.extend(chunk(b"File", info + segment + chunk(b"adlr", struct.pack("<I", zlib.adler32(payload)))))
    struct.pack_into("<Q", data, 11, len(data))
    return bytes(data) + b"\0" + struct.pack("<Q", len(index)) + index


def verify_log_sharing(harness: Path, output: Path) -> dict:
    log = output / "test_shared.log"
    process = subprocess.Popen([str(harness), str(log)], cwd=output, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if process.stdout.readline().strip() != "READY":
            raise RuntimeError("Log writer did not become ready")
        if process.poll() is not None:
            raise RuntimeError("Log writer exited before the sharing check")
        if log.read_bytes() != b"LIVE log writer\n":
            raise RuntimeError("External reader did not receive the flushed log")
        try:
            with log.open("wb"):
                pass
        except PermissionError:
            pass
        else:
            raise RuntimeError("A second log writer must be denied")
        if process.poll() is not None:
            raise RuntimeError("Log writer exited during the sharing check")
    finally:
        process.communicate(input="\n", timeout=10)
    if process.returncode:
        raise RuntimeError(f"Log writer failed with code {process.returncode}")
    print("PASS live log reads, flushed content, exclusive writes", flush=True)
    return {"external_read_while_open": True, "flushed_content_visible": True, "second_writer_denied": True}


def verify_injection(launcher: Path, output: Path, setup: Path) -> dict:
    work = output / ('injection_probe_' + uuid.uuid4().hex[:12])
    work.mkdir()
    patch = work / 'chs_patch'
    patch.mkdir()
    compile_source(work, 'DC4_FD.exe', 'test_injection.cpp', ['/link', '/SUBSYSTEM:CONSOLE', '/OUT:DC4_FD.exe'], setup)
    compile_source(work, 'dc4_fd_chs.dll', 'test_injection.cpp', ['/DPROBE_DLL', '/LD', '/link', '/DEF:' + str(ROOT / 'src/runtime.def'), '/OUT:dc4_fd_chs.dll'], setup)
    shutil.copy2(launcher, work / launcher.name)
    (patch / 'patch.xp3').write_bytes(b'probe')
    (patch / 'config.ini').write_text('[runtime]\n', encoding='utf-8')
    result = subprocess.run([str(work / launcher.name), '--no-dialog', '--wait'], cwd=work, timeout=45)
    log = (patch / 'launcher.log').read_text('utf-8')
    if result.returncode or 'READY runtime initialized; game resumed' not in log:
        raise RuntimeError(f'Suspended-host injection failed with code {result.returncode}; see {patch / "launcher.log"}')
    if (patch / 'runtime_probe.log').read_bytes() != b'INITIALIZED before host entry\n':
        raise RuntimeError('Runtime initialization did not precede the host entry point')
    print('PASS suspended x86 host injection and initialization before its entry point', flush=True)
    return {'suspended_host': True, 'runtime_initialized_before_host_entry': True, 'game_started': False, 'log': str(patch / 'launcher.log')}


def build(out: Path, verify: bool = True) -> dict:
    out = validate_output(out)
    out.mkdir(parents=True, exist_ok=True)
    setup = vcvarsall()
    dll = compile_source(out, "dc4_fd_chs.dll", "patch.cpp", ["/LD", "/link", "/DEF:" + str(ROOT / "src/runtime.def"), "/OUT:dc4_fd_chs.dll", "gdi32.lib", "user32.lib"], setup)
    launcher = compile_source(out, "DC4_FD_chs.exe", "launcher.cpp", ["/link", "/SUBSYSTEM:WINDOWS", "/OUT:DC4_FD_chs.exe", "shell32.lib", "user32.lib"], setup)
    summary = {"dll": pe_summary(dll), "launcher": pe_summary(launcher), "compiler_setup": str(setup), "stream_tests": False, "game_started": False}
    if summary["dll"]["exports"] != ["StartRuntime"]:
        raise ValueError("Runtime must export exactly StartRuntime")
    if verify:
        harness = compile_source(out, "test_stream.exe", "test_stream.cpp", ["/link", "/SUBSYSTEM:CONSOLE", "/OUT:test_stream.exe"], setup)
        good = out / "test_plain.xp3"
        bad = out / "test_malformed.xp3"
        good.write_bytes(fixture())
        malformed = bytearray(fixture())
        struct.pack_into("<Q", malformed, 11, 0xFFFFFFFFFFFFFFFF)
        bad.write_bytes(malformed)
        subprocess.run([str(harness), str(good), str(bad)], cwd=out, check=True)
        summary["stream_tests"] = True
        from solution.unpack.xp3 import pack_xp3
        nested = compile_source(out, 'test_nested_stream.exe', 'test_nested_stream.cpp',
            ['/link', '/SUBSYSTEM:CONSOLE', '/OUT:test_nested_stream.exe', 'gdi32.lib', 'user32.lib', 'shell32.lib'], setup)
        nested_input = out / 'test_nested_input/uipsd'
        nested_input.mkdir(parents=True)
        (nested_input / 'window.pimg').write_bytes(b'PATCH')
        nested_packet = out / 'test_nested_stream.xp3'
        pack_xp3(nested_input.parent, nested_packet, compress_index=False, compress_content=False)
        subprocess.run([str(nested), str(nested_packet)], cwd=out, check=True)
        summary['nested_stream_fallback_tests'] = True
        log_harness = compile_source(out, "test_log_share.exe", "test_log_share.cpp", ["/link", "/SUBSYSTEM:CONSOLE", "/OUT:test_log_share.exe"], setup)
        summary["log_sharing_tests"] = verify_log_sharing(log_harness, out)
        summary["injection_tests"] = verify_injection(launcher, out, setup)
    (out / "runtime_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[done] runtime={dll}", flush=True)
    print(f"[done] launcher={launcher}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify the DC4 FD x86 localization runtime without starting the game.")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    build(args.out_dir, not args.skip_tests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
