from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from solution.unpack.xp3 import ArchiveError, extract_archive, extract_entry, pack_xp3, read_index


def chunk(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack('<Q', len(data)) + data


def fixture(path: Path, name: str, data: bytes, *, protected: bool = False, checksum: int | None = None, declared_size: int | None = None) -> None:
    signature = bytes.fromhex('58 50 33 0d 0a 20 0a 1a 8b 67 01')
    packed = zlib.compress(data)
    info = struct.pack('<IQQH', 0x80000000 if protected else 0, len(data) if declared_size is None else declared_size, len(packed), len(name.encode('utf-16-le')) // 2)
    info += name.encode('utf-16-le')
    segment = struct.pack('<IQQQ', 1, 19, len(data), len(packed))
    checksum = zlib.adler32(data) if checksum is None else checksum
    entry = chunk(b'File', chunk(b'info', info) + chunk(b'segm', segment) + chunk(b'adlr', struct.pack('<I', checksum)))
    index = zlib.compress(entry)
    index_offset = 19 + len(packed)
    path.write_bytes(signature + struct.pack('<Q', index_offset) + packed + b'\x01' + struct.pack('<QQ', len(index), len(entry)) + index)


class Xp3Tests(unittest.TestCase):
    def test_index_records_inconsistent_declared_size_without_hiding_entry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'notice.xp3'
            fixture(archive, 'notice.txt', b'notice', declared_size=910)
            entry = read_index(archive)[0]
            self.assertEqual(entry.size, 910)
            with self.assertRaisesRegex(ArchiveError, 'size mismatch'):
                extract_entry(archive, entry)

    def test_reads_independent_compressed_fixture_with_unicode_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / 'sample.xp3'
            payload = '中文、日本語\n'.encode('utf-8')
            fixture(archive, 'scn/测试.ks', payload)
            entries = read_index(archive)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].name, 'scn/测试.ks')
            self.assertEqual(extract_entry(archive, entries[0], verify_checksum=True), payload)

    def test_pack_roundtrip_preserves_nested_names_empty_and_binary_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source'
            (source / 'scn').mkdir(parents=True)
            expected = {'scn/中文.ks.scn': bytes(range(256)) * 9, 'empty': b'', 'plain.txt': b'hello\x00world'}
            for name, data in expected.items():
                (source / name).write_bytes(data)
            for compressed in (False, True):
                archive = root / f'{compressed}.xp3'
                summary = pack_xp3(source, archive, compress_content=compressed, compress_index=compressed)
                self.assertEqual(summary['file_count'], len(expected))
                output = root / str(compressed)
                extract_archive(archive, output, verify_checksum=True)
                self.assertEqual({p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob('*') if p.is_file()}, expected)

    def test_traversal_is_rejected_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('../outside', '/absolute', 'C:/absolute', 'scn/../../escape', 'file:stream'):
                archive = root / 'bad.xp3'
                fixture(archive, name, b'bad')
                output = root / 'output'
                with self.assertRaises(ArchiveError, msg=name):
                    extract_archive(archive, output)
                self.assertFalse(output.exists())

    def test_windows_aliases_and_devices_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('same.txt.', 'same.txt ', 'dir./file.txt', 'CON', 'nul.txt', 'aux/file.bin', 'LPT1.bin', 'COM9.txt', 'bad?name'):
                archive = root / 'bad.xp3'
                fixture(archive, name, b'bad')
                with self.assertRaises(ArchiveError, msg=name):
                    extract_archive(archive, root / 'output')
                self.assertFalse((root / 'output').exists())

    def test_encrypted_entry_requires_explicit_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / 'encrypted.xp3'
            fixture(archive, 'secret.bin', b'opaque', protected=True)
            with self.assertRaisesRegex(ArchiveError, 'scheme'):
                extract_archive(archive, root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_checksum_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'bad.xp3'
            fixture(archive, 'file.bin', b'payload', checksum=0)
            with self.assertRaisesRegex(ArchiveError, 'checksum'):
                extract_entry(archive, read_index(archive)[0], verify_checksum=True)

    def test_existing_file_is_preserved_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture(root / 'file.xp3', 'keep.txt', b'new')
            output = root / 'output'
            output.mkdir()
            (output / 'keep.txt').write_bytes(b'original')
            with self.assertRaises(ArchiveError):
                extract_archive(root / 'file.xp3', output)
            self.assertEqual((output / 'keep.txt').read_bytes(), b'original')

    def test_out_of_bounds_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'bad.xp3'
            archive.write_bytes(bytes.fromhex('58 50 33 0d 0a 20 0a 1a 8b 67 01') + struct.pack('<Q', 1 << 50))
            with self.assertRaises(ArchiveError):
                read_index(archive)


if __name__ == '__main__':
    unittest.main()
