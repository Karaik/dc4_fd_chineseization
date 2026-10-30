from __future__ import annotations

import mmap
import re
import stat
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol


SIGNATURE = bytes.fromhex('58 50 33 0d 0a 20 0a 1a 8b 67 01')
MAX_INDEX_SIZE = 64 * 1024 * 1024


class ArchiveError(ValueError):
    pass


class Scheme(Protocol):
    def decrypt(self, data: bytes, file_hash: int, offset: int = 0) -> bytes: ...


@dataclass(frozen=True)
class Segment:
    flags: int
    offset: int
    size: int
    packed_size: int


@dataclass(frozen=True)
class Entry:
    name: str
    flags: int
    size: int
    packed_size: int
    file_hash: int | None
    segments: tuple[Segment, ...]

    @property
    def protected(self) -> bool:
        return bool(self.flags & 0x80000000)


def _check_bounds(data: bytes | mmap.mmap, offset: int, size: int) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ArchiveError(f'Archive range out of bounds: offset={offset} size={size}')


def _range(data: bytes | mmap.mmap, offset: int, size: int) -> bytes:
    _check_bounds(data, offset, size)
    return bytes(data[offset:offset + size])


def _number(data: bytes | mmap.mmap, offset: int, fmt: str) -> int:
    return struct.unpack(fmt, _range(data, offset, struct.calcsize(fmt)))[0]


def _index_blob(data: mmap.mmap) -> bytes:
    if _range(data, 0, 11) != SIGNATURE:
        raise ArchiveError('Invalid XP3 signature')
    offset = _number(data, 11, '<Q')
    if _number(data, offset, '<I') == 0x80:
        offset = _number(data, offset + 9, '<Q')
    kind = _number(data, offset, '<B')
    if kind == 0:
        size = _number(data, offset + 1, '<Q')
        if size > MAX_INDEX_SIZE:
            raise ArchiveError('XP3 index is too large')
        return _range(data, offset + 9, size)
    if kind == 1:
        packed_size = _number(data, offset + 1, '<Q')
        size = _number(data, offset + 9, '<Q')
        if size > MAX_INDEX_SIZE or packed_size > MAX_INDEX_SIZE:
            raise ArchiveError('XP3 index is too large')
        try:
            decoder = zlib.decompressobj()
            blob = decoder.decompress(_range(data, offset + 17, packed_size), size + 1)
        except zlib.error as exc:
            raise ArchiveError('Invalid compressed XP3 index') from exc
        if len(blob) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ArchiveError('XP3 index decompressed size mismatch')
        return blob
    raise ArchiveError(f'Unsupported XP3 index type: {kind}')


def _chunks(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    pos = start
    while pos < end:
        if pos + 12 > end:
            raise ArchiveError('Truncated XP3 chunk header')
        tag = _range(data, pos, 4)
        size = _number(data, pos + 4, '<Q')
        body = pos + 12
        next_pos = body + size
        if next_pos > end:
            raise ArchiveError('Truncated XP3 chunk body')
        yield tag, body, next_pos
        pos = next_pos


def read_index(path: Path) -> list[Entry]:
    path = Path(path)
    if path.stat().st_size < 19:
        raise ArchiveError('XP3 file is too short')
    with path.open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        index = _index_blob(data)
        result = []
        for tag, start, end in _chunks(index):
            if tag != b'File':
                continue
            info = None
            file_hash = None
            segments = []
            for subtag, body, subend in _chunks(index, start, end):
                size = subend - body
                if subtag == b'info':
                    if info is not None or size < 22:
                        raise ArchiveError('Invalid or duplicate XP3 info chunk')
                    flags, unpacked, packed, length = struct.unpack('<IQQH', _range(index, body, 22))
                    if 22 + length * 2 > size or not length:
                        raise ArchiveError('Invalid XP3 filename length')
                    try:
                        name = _range(index, body + 22, length * 2).decode('utf-16-le')
                    except UnicodeDecodeError as exc:
                        raise ArchiveError('Invalid XP3 filename encoding') from exc
                    info = name, flags, unpacked, packed
                elif subtag == b'segm':
                    if size % 28:
                        raise ArchiveError('Invalid XP3 segment table size')
                    for pos in range(body, subend, 28):
                        segment = Segment(*struct.unpack('<IQQQ', _range(index, pos, 28)))
                        _check_bounds(data, segment.offset, segment.packed_size)
                        if segment.flags & 7 not in (0, 1):
                            raise ArchiveError('Unsupported XP3 segment compression')
                        segments.append(segment)
                elif subtag == b'adlr':
                    if size != 4 or file_hash is not None:
                        raise ArchiveError('Invalid XP3 checksum chunk')
                    file_hash = _number(index, body, '<I')
            if info is None or not segments:
                raise ArchiveError('XP3 entry has no info or segment table')
            name, flags, size, packed_size = info
            result.append(Entry(name, flags, size, packed_size, file_hash, tuple(segments)))
    if not result:
        raise ArchiveError('XP3 contains no file entries')
    return result


def _extract(data: mmap.mmap, entry: Entry, scheme: Scheme | None, verify_checksum: bool) -> bytes:
    if sum(x.size for x in entry.segments) != entry.size or sum(x.packed_size for x in entry.segments) != entry.packed_size:
        raise ArchiveError(f'XP3 entry size mismatch: {entry.name}')
    if entry.protected and scheme is None:
        raise ArchiveError(f'Protected entry requires a decryption scheme: {entry.name}')
    if entry.protected and entry.file_hash is None:
        raise ArchiveError(f'Protected entry has no file hash: {entry.name}')
    blocks = []
    offset = 0
    for segment in entry.segments:
        block = _range(data, segment.offset, segment.packed_size)
        if segment.flags & 7 == 1:
            try:
                block = zlib.decompress(block)
            except zlib.error as exc:
                raise ArchiveError(f'Invalid compressed segment: {entry.name}') from exc
        if len(block) != segment.size:
            raise ArchiveError(f'XP3 segment size mismatch: {entry.name}')
        if entry.protected:
            block = scheme.decrypt(block, entry.file_hash, offset)
            if len(block) != segment.size:
                raise ArchiveError(f'Decryption changed entry length: {entry.name}')
        blocks.append(block)
        offset += segment.size
    result = b''.join(blocks)
    if verify_checksum and entry.file_hash is not None and zlib.adler32(result) != entry.file_hash:
        raise ArchiveError(f'XP3 checksum mismatch: {entry.name}')
    return result


def extract_entry(path: Path, entry: Entry, scheme: Scheme | None = None, *, verify_checksum: bool = False) -> bytes:
    with Path(path).open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        return _extract(data, entry, scheme, verify_checksum)


def safe_name(value: str) -> str:
    name = value.replace('\\', '/')
    parts = name.split('/')
    devices = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$', re.IGNORECASE)
    if not name or name.startswith('/') or any(part in ('', '.', '..') or part.endswith(('.', ' ')) or any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part) or devices.fullmatch(part) for part in parts):
        raise ArchiveError(f'Unsafe archive path: {value!r}')
    return PurePosixPath(*parts).as_posix()


def _check_output_path(root: Path, destination: Path) -> None:
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ArchiveError(f'Archive output escapes destination: {destination}')
    for parent in (destination, *destination.parents):
        if parent.exists():
            info = parent.lstat()
            if parent.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ArchiveError(f'Archive output contains a link or reparse point: {parent}')
        if parent == root:
            break


def extract_archive(path: Path, output: Path, scheme: Scheme | None = None, *, select: Callable[[Entry], bool] | None = None, overwrite: bool = False, verify_checksum: bool = False) -> list[dict]:
    entries = [e for e in read_index(path) if select is None or select(e)]
    names = [safe_name(e.name) for e in entries]
    if len({n.casefold() for n in names}) != len(names):
        raise ArchiveError('Duplicate archive output paths')
    if scheme is None and any(e.protected for e in entries):
        raise ArchiveError('Protected archive entries require a decryption scheme')
    output = Path(output).absolute()
    for name in names:
        destination = output / name
        _check_output_path(output, destination)
        if destination.exists() and not overwrite:
            raise ArchiveError(f'Archive output already exists: {destination}')
    import hashlib
    result = []
    with Path(path).open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for entry, name in zip(entries, names):
            payload = _extract(data, entry, scheme, verify_checksum)
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _check_output_path(output, destination)
            destination.write_bytes(payload)
            result.append({'name':name, 'bytes':len(payload), 'sha256':hashlib.sha256(payload).hexdigest(), 'protected':entry.protected})
    return result


def _chunk(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack('<Q', len(data)) + data


def pack_xp3(source: Path, output: Path, *, compress_content: bool = False, compress_index: bool = True) -> dict:
    source = Path(source).resolve()
    files = sorted(p for p in source.rglob('*') if p.is_file())
    if not files:
        raise ArchiveError('No files to pack')
    names = [safe_name(p.relative_to(source).as_posix()) for p in files]
    if len({n.casefold() for n in names}) != len(names):
        raise ArchiveError('Duplicate archive input paths')
    output = Path(output).absolute()
    if output.resolve().is_relative_to(source):
        raise ArchiveError('Archive output must not be inside its input tree')
    for path in files:
        _check_output_path(source, path)
    output.parent.mkdir(parents=True, exist_ok=True)
    index = bytearray()
    with output.open('wb') as handle:
        handle.write(SIGNATURE + struct.pack('<Q', 0))
        for path, name in zip(files, names):
            payload = path.read_bytes()
            packed = zlib.compress(payload) if compress_content else payload
            offset = handle.tell()
            handle.write(packed)
            encoded = name.encode('utf-16-le')
            info = struct.pack('<IQQH', 0, len(payload), len(packed), len(encoded) // 2) + encoded
            segment = struct.pack('<IQQQ', int(compress_content), offset, len(payload), len(packed))
            body = _chunk(b'info', info) + _chunk(b'segm', segment) + _chunk(b'adlr', struct.pack('<I', zlib.adler32(payload)))
            index.extend(_chunk(b'File', body))
        index_offset = handle.tell()
        if compress_index:
            compressed_index = zlib.compress(index)
            handle.write(b'\x01' + struct.pack('<QQ', len(compressed_index), len(index)) + compressed_index)
        else:
            handle.write(b'\x00' + struct.pack('<Q', len(index)) + index)
        handle.seek(11)
        handle.write(struct.pack('<Q', index_offset))
    return {'file_count':len(files), 'bytes':output.stat().st_size, 'compress_content':compress_content, 'compress_index':compress_index}
