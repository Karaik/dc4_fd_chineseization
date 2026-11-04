"""原始 TLG、JPEG 与 CUR 的独立回写及像素验证。"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import Iterable

from PIL import Image, IcoImagePlugin


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0/lib'
SOURCE = Path(__file__).with_name('src') / 'NativeImages.cs'


class NativeImageError(ValueError):
    pass


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(width: int, height: int, rgba: bytes) -> dict:
    return {'width': width, 'height': height, 'rgba_sha256': _hash(rgba)}


def _pil_rgba(path: Path) -> tuple[tuple[int, int], bytes]:
    try:
        with Image.open(path) as image:
            return image.size, image.convert('RGBA').tobytes()
    except (OSError, ValueError) as error:
        raise NativeImageError(f'Cannot decode image {path}: {error}') from error


def _output(target: Path, *inputs: Path) -> Path:
    target = Path(target)
    if any(target.resolve() == Path(path).resolve() for path in inputs):
        raise NativeImageError('Output must differ from all source files')
    if target.exists() or target.is_symlink():
        raise NativeImageError(f'Output already exists: {target}')
    return target


def _write_new(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(payload)


def _helper() -> Path:
    """按维护源码和依赖摘要复用编译产物，不使用临时研究源码。"""
    library = TOOLS / 'TlgLib.dll'
    if not SOURCE.is_file() or not library.is_file():
        raise NativeImageError(f'Missing native image source or TLG library: {SOURCE}, {library}')
    signature = _hash(SOURCE.read_bytes() + library.read_bytes())[:24]
    cache = ROOT / 'solution/patch/work/native_images' / signature
    executable = cache / 'NativeImages.exe'
    if executable.is_file():
        return executable
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise NativeImageError(f'.NET Framework C# compiler is missing: {compiler}')
    cache.mkdir(parents=True, exist_ok=True)
    # 每轮编译使用独占临时文件，避免两个审计进程互相截断产物。
    with tempfile.TemporaryDirectory(prefix='compile_', dir=cache) as folder:
        compiled = Path(folder) / 'NativeImages.exe'
        result = subprocess.run([str(compiler), '/nologo', '/optimize+', '/target:exe',
                                 '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll',
                                 '/out:' + str(compiled), str(SOURCE)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise NativeImageError('Native image compiler failed:\n' + result.stdout + result.stderr)
        try:
            os.link(compiled, executable)
        except FileExistsError:
            pass
    return executable


def _native(mode: str, requests: list[dict]) -> list[dict]:
    if not requests:
        return []
    executable = _helper()
    request = {'mode': mode, 'library': str(TOOLS / 'TlgLib.dll'), 'items': requests}
    with tempfile.TemporaryDirectory(prefix='request_', dir=executable.parent) as folder:
        input_path, output_path = Path(folder) / 'input.json', Path(folder) / 'output.json'
        input_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([str(executable), str(input_path), str(output_path)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=max(300, len(requests) * 30))
        if result.returncode or not output_path.is_file():
            raise NativeImageError('Native image helper failed:\n' + result.stdout + result.stderr)
        values = json.loads(output_path.read_text('utf-8-sig'))
    for value in values:
        if 'error' in value:
            raise NativeImageError(value['error'])
    return values


def read_rgba_many(paths: Iterable[Path]) -> dict[str, dict]:
    """批量读取像素身份；键为调用者传入路径的字符串，TLG 只启一个子进程。"""
    result: dict[str, dict] = {}
    tlg = []
    for path in paths:
        path = Path(path)
        key = str(path)
        if path.suffix.lower() == '.tlg':
            tlg.append({'path': str(path.resolve()), 'key': key})
        else:
            size, rgba = _pil_rgba(path)
            result[key] = {**_identity(*size, rgba), 'file_sha256': _hash(path.read_bytes())}
    for item in _native('read', tlg):
        key = item.pop('key')
        result[key] = item
    return result


def read_rgba(path: Path) -> dict:
    return read_rgba_many([path])[str(Path(path))]


def encode_tlg_many(jobs: Iterable[dict]) -> list[dict]:
    """jobs 含 png、target、original 路径；force=True 可检验无改动全量重编码。"""
    requests = []
    targets = set()
    for job in jobs:
        png, original = Path(job['png']), Path(job['original'])
        target = _output(Path(job['target']), png, original)
        if target.resolve() in targets:
            raise NativeImageError(f'Duplicate output: {target}')
        targets.add(target.resolve())
        size, rgba = _pil_rgba(png)
        requests.append({'png': str(png.resolve()), 'target': str(target.resolve()), 'original': str(original.resolve()),
                         'force': bool(job.get('force', False)), 'expected_rgba_sha256': _hash(rgba)})
    return _native('tlg', requests)


def encode_tlg(png: Path, target: Path, original: Path) -> dict:
    return encode_tlg_many([{'png': png, 'target': target, 'original': original}])[0]


def encode_jpeg(png: Path, target: Path, original: Path) -> dict:
    """以 quality=100、无色度抽样写入真正 JPEG，并记录实际像素误差。"""
    png, original = Path(png), Path(original)
    target = _output(target, png, original)
    original_bytes = original.read_bytes()
    size, rgba = _pil_rgba(png)
    source_size, source_rgba = _pil_rgba(original)
    if size != source_size:
        raise NativeImageError(f'JPEG dimensions differ: {size} != {source_size}')
    if any(alpha != 255 for alpha in rgba[3::4]):
        raise NativeImageError('JPEG cannot preserve a non-opaque alpha channel')
    changed = rgba != source_rgba
    encoded = original_bytes
    decoded_rgba = rgba
    if changed:
        buffer = io.BytesIO()
        Image.frombytes('RGBA', size, rgba).convert('RGB').save(buffer, format='JPEG', quality=100, subsampling=0)
        encoded = buffer.getvalue()
        with Image.open(io.BytesIO(encoded)) as decoded:
            if decoded.format != 'JPEG' or decoded.size != size:
                raise NativeImageError('Encoded JPEG format or dimensions differ')
            decoded_rgba = decoded.convert('RGBA').tobytes()
    rgb_errors = [abs(a - b) for index, (a, b) in enumerate(zip(rgba, decoded_rgba)) if index % 4 != 3]
    alpha_changed = sum(a != b for a, b in zip(rgba[3::4], decoded_rgba[3::4]))
    if alpha_changed:
        raise NativeImageError('JPEG output alpha differs')
    if original.read_bytes() != original_bytes:
        raise NativeImageError('JPEG source changed during encoding')
    _write_new(target, encoded)
    return {**_identity(*size, decoded_rgba), 'input_rgba_sha256': _hash(rgba),
            'file_sha256': _hash(encoded), 'source_sha256': _hash(original_bytes), 'changed': changed,
            'codec': 'JPEG', 'quality': 100, 'subsampling': 0, 'lossless': not changed,
            'max_rgb_error': max(rgb_errors, default=0), 'mean_rgb_error': sum(rgb_errors) / len(rgb_errors),
            'changed_rgb_channels': sum(error != 0 for error in rgb_errors),
            'alpha_changed_pixels': alpha_changed}


def _parse_cursor(data: bytes) -> list[dict]:
    if len(data) < 6:
        raise NativeImageError('Truncated CUR header')
    reserved, kind, count = struct.unpack_from('<HHH', data)
    if reserved != 0 or kind != 2 or not count or len(data) < 6 + count * 16:
        raise NativeImageError('Invalid CUR directory')
    frames = []
    spans = []
    for index in range(count):
        entry = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
        wd, hd, colors, unused, hx, hy, length, offset = entry
        width, height = wd or 256, hd or 256
        if unused or hx >= width or hy >= height:
            raise NativeImageError(f'Invalid CUR frame {index} hotspot or reserved value')
        if offset < 6 + count * 16 or length < 40 or offset + length > len(data):
            raise NativeImageError(f'Invalid CUR frame {index} payload range')
        if any(offset < end and offset + length > start for start, end in spans):
            raise NativeImageError(f'Overlapping CUR frame {index} payload')
        spans.append((offset, offset + length))
        payload = data[offset:offset + length]
        header, dw, dh, planes, bpp, compression, image_size, xp, yp, used, important = struct.unpack_from('<IiiHHIIiiII', payload)
        mask_stride = ((width + 31) // 32) * 4
        pixel_bytes = width * height * 4
        if (header, dw, dh, planes, bpp, compression, used) != (40, width, height * 2, 1, 32, 0, 0):
            raise NativeImageError(f'Unsupported CUR frame {index}: expected uncompressed 32bpp DIB')
        if len(payload) != 40 + pixel_bytes + mask_stride * height:
            raise NativeImageError(f'Invalid CUR frame {index} DIB size')
        rgba = bytearray()
        for y in range(height - 1, -1, -1):
            row = payload[40 + y * width * 4:40 + (y + 1) * width * 4]
            for x in range(0, len(row), 4):
                b, g, r, a = row[x:x + 4]
                rgba.extend((r, g, b, a))
        mask = payload[40 + pixel_bytes:]
        for y in range(height):
            for x in range(width):
                if mask[(height - 1 - y) * mask_stride + x // 8] & (0x80 >> (x % 8)) and rgba[(y * width + x) * 4 + 3]:
                    raise NativeImageError(f'CUR frame {index} AND mask conflicts with alpha')
        # 独立 ICO 解码器复验手写 DIB 解析，包括透明像素的 RGB。
        ico = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', wd, hd, colors, unused, 1, bpp, length, 22) + payload
        try:
            decoded = IcoImagePlugin.IcoFile(io.BytesIO(ico)).frame(0).convert('RGBA')
        except (OSError, ValueError) as error:
            raise NativeImageError(f'CUR frame {index} independent decode failed: {error}') from error
        if decoded.size != (width, height) or decoded.tobytes() != rgba:
            raise NativeImageError(f'CUR frame {index} independent RGBA comparison failed')
        frames.append({**_identity(width, height, bytes(rgba)), 'index': index, 'hotspot_x': hx, 'hotspot_y': hy,
                       'frame_sha256': _hash(payload), 'and_mask_sha256': _hash(mask),
                       '_entry': entry, '_payload': payload, '_rgba': bytes(rgba)})
    return frames


def read_cursor(path: Path) -> dict:
    data = Path(path).read_bytes()
    frames = _parse_cursor(data)
    return {'file_sha256': _hash(data), 'frame_count': len(frames),
            'frames': [{key: value for key, value in frame.items() if not key.startswith('_')} for frame in frames]}


def _dib(width: int, height: int, rgba: bytes) -> bytes:
    pixels = bytearray()
    mask_stride = ((width + 31) // 32) * 4
    masks = bytearray()
    for y in range(height - 1, -1, -1):
        mask = bytearray(mask_stride)
        for x in range(width):
            offset = (y * width + x) * 4
            r, g, b, a = rgba[offset:offset + 4]
            pixels.extend((b, g, r, a))
            if a == 0:
                mask[x // 8] |= 0x80 >> (x % 8)
        masks.extend(mask)
    return struct.pack('<IiiHHIIiiII', 40, width, height * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0) + pixels + masks


def rebuild_cursor(original: Path, edits: dict[int, Path], target: Path) -> dict:
    """重建 CUR 目录和变更帧；未改帧负载逐字节保留，热点始终取自原文件。"""
    original = Path(original)
    target = _output(target, original, *edits.values())
    source = original.read_bytes()
    frames = _parse_cursor(source)
    if any(type(index) is not int or index < 0 or index >= len(frames) for index in edits):
        raise NativeImageError('Unknown CUR frame index')
    changed_frames = []
    for index, path in edits.items():
        frame = frames[index]
        size, rgba = _pil_rgba(path)
        if size != (frame['width'], frame['height']):
            raise NativeImageError(f'CUR frame {index} dimensions differ: {size}')
        if rgba != frame['_rgba']:
            frame['_payload'] = _dib(*size, rgba)
            frame['_rgba'] = rgba
            changed_frames.append(index)
    output = source
    if changed_frames:
        offset = 6 + 16 * len(frames)
        directory = bytearray(struct.pack('<HHH', 0, 2, len(frames)))
        for frame in frames:
            entry = frame['_entry']
            directory.extend(struct.pack('<BBBBHHII', *entry[:6], len(frame['_payload']), offset))
            offset += len(frame['_payload'])
        output = bytes(directory) + b''.join(frame['_payload'] for frame in frames)
    reread = _parse_cursor(output)
    for before, after in zip(frames, reread):
        if before['_rgba'] != after['_rgba'] or before['_entry'][:6] != after['_entry'][:6]:
            raise NativeImageError('Rebuilt CUR pixels or hotspots differ')
        if before['index'] not in changed_frames and before['_payload'] != after['_payload']:
            raise NativeImageError('Unchanged CUR frame payload differs')
    if original.read_bytes() != source:
        raise NativeImageError('CUR source changed during encoding')
    _write_new(target, output)
    return {'changed': bool(changed_frames), 'changed_frames': sorted(changed_frames),
            'file_sha256': _hash(output), 'source_sha256': _hash(source), 'frame_count': len(frames),
            'frames': [{key: value for key, value in frame.items() if not key.startswith('_')} for frame in reread]}
