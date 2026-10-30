from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import zlib
from datetime import datetime, timezone
from pathlib import Path

from solution.decrypt.cx import load_scheme
from solution.unpack.xp3 import extract_entry, read_index


TITLE_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = (
    ("data.xp3", "AppConfig.tjs", "8792f1ed36c03598d3d59718c884f4c0968eab518d59ea1494fb1b55dcb96a99"),
    ("data.xp3", "main/custom.ks", "160594075557716c6a564eadebc540927c8fca250e6106aacf34a3f550a33919"),
    ("data.xp3", "startup.tjs", "25fbd9f90fa7e7a783b1c4485b86e0004257dc056475623ef79bd11a304d0cf5"),
    ("data.xp3", "scn/4fd_asa191109a.ks.scn", "4199098ffeac9ab57a7d65d8cbb0556f1f46501a729b604031e558d8710a7d04"),
    ("data.xp3", "uipsd/backlog.pimg", "069930e0ee2ff64ed3facadbe563ede7d7c842b844fab4eace5b8ab3b610e790"),
    ("data.xp3", "image/comu/comuicon_empty.png", "9d8f9476c91754602ac9a4360c729b13ce259a249a6192f79a8edf206da47c01"),
    ("data.xp3", "bgm/bgm001.opus", "fc3ccf08dd9d3d21d1de266276bd7594c8017d59b2a95cd65c88d56c1634e425"),
    ("bgimage.xp3", "bg001a.png", "995931a429c4471f81c4bbcc9cdbc802d3a3f5e6411bb7369bfc56ab306fecd8"),
    ("voice.xp3", "4fd_asa201107a_asa001.opus", "69503e0474578958410aaca06fde31361e445526d3af5063fe89ca3192e1a55b"),
    ("evimage.xp3", "ev_4fd_asa01a.png", None),
    ("fgimage.xp3", "KotoRI/kotori1_1_10.tlg", None),
)


def _validate_payload(name: str, data: bytes, ffmpeg: str) -> dict:
    if name.endswith(".png"):
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return {"validation": "png_full_decode", "dimensions": list(image.size), "mode": image.mode}
    if name.endswith(".opus"):
        if not data.startswith(b"OggS") or b"OpusHead" not in data[:256]:
            raise ValueError(f"Invalid Ogg Opus header: {name}")
        cache = TITLE_ROOT / "tmp/decrypt/samples"
        cache.mkdir(parents=True, exist_ok=True)
        sample_path = cache / Path(name).name
        sample_path.write_bytes(data)
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(sample_path), "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        if result.stderr.strip():
            raise ValueError(f"Audio decoder reported errors: {result.stderr.strip()}")
        return {"validation": "opus_full_decode"}
    if data.startswith(b"PSB\0"):
        if len(data) < 44 or struct.unpack_from("<H", data, 4)[0] != 3:
            raise ValueError(f"Unsupported sample PSB header: {name}")
        offsets = struct.unpack_from("<8I", data, 8)
        if offsets[0] != 44 or any(value > len(data) for value in offsets):
            raise ValueError(f"Invalid PSB sample offsets: {name}")
        return {"validation": "psb_v3_header_bounds", "version": 3}
    if data.startswith(b"TJS2100\0"):
        if struct.unpack_from("<I", data, 8)[0] != len(data):
            raise ValueError(f"Invalid TJS2 size: {name}")
        return {"validation": "tjs2_header_size"}
    if data.startswith(b"\xfe\xfe\x01\xff\xfe"):
        payload = data[5:]
        if len(payload) % 2:
            raise ValueError(f"Odd UTF-16 payload size: {name}")
        decoded = bytearray()
        for (value,) in struct.iter_unpack("<H", payload):
            decoded.extend(struct.pack("<H", ((value & 0xAAAA) >> 1) | ((value & 0x5555) << 1)))
        text = decoded.decode("utf-16-le")
        if not text.strip():
            raise ValueError(f"Empty decoded script text: {name}")
        return {"validation": "krkr_text_type1_utf16", "characters": len(text)}
    if data.startswith((b"TLG5.0", b"TLG6.0")):
        return {"validation": "tlg_signature_only"}
    raise ValueError(f"Unrecognized validation sample: {name}")


def verify_samples() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for full audio decoding")
    scheme = load_scheme()
    indexes = {}
    results = []
    for archive_name, name, expected_sha256 in SAMPLES:
        archive = TITLE_ROOT / "game" / archive_name
        if archive_name not in indexes:
            indexes[archive_name] = {entry.name: entry for entry in read_index(archive)}
        entry = indexes[archive_name][name]
        data = extract_entry(archive, entry, scheme, verify_checksum=True)
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError(f"Known plaintext digest mismatch: {name}")
        actual_adler = zlib.adler32(data) & 0xFFFFFFFF
        if actual_adler != entry.file_hash:
            raise ValueError(f"Original plaintext checksum mismatch: {name}")
        checks = _validate_payload(name, data, ffmpeg)
        results.append({
            "archive": archive_name,
            "name": name,
            "bytes": len(data),
            "adler32": f"{actual_adler:08x}",
            "checksum_matches": True,
            "sha256": digest,
            "known_plaintext_digest_checked": expected_sha256 is not None,
            **checks,
        })
    return {
        "schema_version": 1,
        "project": "dc4_fd_chineseization",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "representative_complete_payloads",
        "archive_count": len(indexes),
        "sample_count": len(results),
        "media_decoded": {
            "png": sum(item["validation"] == "png_full_decode" for item in results),
            "opus": sum(item["validation"] == "opus_full_decode" for item in results),
        },
        "samples": results,
        "limits": [
            "代表样本验证不等于全部封包覆盖",
            "本报告仅检查 PSB 头结构；脚本回环使用独立验证",
            "TLG 样本仅验证签名和原始校验值，未解码图像",
            "本报告不验证游戏运行或签名强制性",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate representative original DC4 payloads and decode PNG/Opus samples")
    parser.add_argument("--report", type=Path, default=Path("tmp/decrypt/verification.json"))
    args = parser.parse_args(argv)
    destination = (TITLE_ROOT / args.report).resolve()
    if not any(destination.is_relative_to(TITLE_ROOT / part) for part in ("tmp", "docs")):
        raise ValueError("Verification reports must stay under project tmp/ or docs/")
    report = verify_samples()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[verified] {report['sample_count']} samples from {report['archive_count']} archives")
    print(f"[report] {destination.relative_to(TITLE_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
