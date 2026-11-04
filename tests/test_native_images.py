from __future__ import annotations

import hashlib
import importlib
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image, IcoImagePlugin


ROOT = Path(__file__).resolve().parents[1]

# Fixed raw TLG5 generated once by FreeMote 4.3.0 from these four RGBA pixels.
TLG5_2X2 = bytes.fromhex(
    '544c47352e30007261771a04020000000200000004000000240000000104000000'
    '0a0000000104000000141e3c000104000000f60000000104000000007fff83'
)
TLG5_RGBA = bytes((10, 20, 30, 0, 40, 50, 60, 127, 70, 80, 90, 255, 100, 110, 120, 1))


def fixture_cursor() -> bytes:
    # Two hand-built 2x2 bottom-up DIB frames; hidden RGB must survive.
    frames = []
    for color in ((10, 20, 30, 0), (70, 80, 90, 255)):
        r, g, b, a = color
        dib = struct.pack('<IiiHHIIiiII', 40, 2, 4, 1, 32, 0, 16, 0, 0, 0, 0)
        frames.append(dib + bytes((b, g, r, a)) * 4 + (b'\xc0\0\0\0' if a == 0 else b'\0' * 4) * 2)
    table = b''
    offset = 38
    for index, frame in enumerate(frames):
        table += struct.pack('<BBBBHHII', 2, 2, 0, 0, index, 1, len(frame), offset)
        offset += len(frame)
    return struct.pack('<HHH', 0, 2, 2) + table + b''.join(frames)


def cursor_frame(blob: bytes, index: int) -> tuple[bytes, tuple[int, ...], bytes]:
    entry = struct.unpack_from('<BBBBHHII', blob, 6 + index * 16)
    wd, hd, colors, unused, hx, hy, length, offset = entry
    frame = blob[offset:offset + length]
    ico = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', wd, hd, colors, unused, 1, 32, length, 22) + frame
    rgba = IcoImagePlugin.IcoFile(io.BytesIO(ico)).frame(0).convert('RGBA').tobytes()
    return frame, entry, rgba


class NativeImagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'solution/patch/native_images.py').is_file():
            raise AssertionError('Missing native image implementation')
        cls.native = importlib.import_module('solution.patch.native_images')

    def test_rgba_hash_includes_hidden_rgb_and_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hidden.png'
            Image.frombytes('RGBA', (2, 1), bytes((10, 20, 30, 0, 40, 50, 60, 127))).save(path)
            actual = self.native.read_rgba(path)
            self.assertEqual((actual['width'], actual['height']), (2, 1))
            self.assertEqual(actual['rgba_sha256'], hashlib.sha256(bytes((10, 20, 30, 0, 40, 50, 60, 127))).hexdigest())

    def test_cursor_edit_preserves_untouched_frame_and_hotspots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'old.cur'
            blob = fixture_cursor()
            original.write_bytes(blob)
            edit = root / 'edit.png'
            rgba = bytes((12, 34, 56, 0, 40, 50, 60, 127, 1, 2, 3, 255, 4, 5, 6, 1))
            Image.frombytes('RGBA', (2, 2), rgba).save(edit)
            result = self.native.rebuild_cursor(original, {0: edit}, root / 'new.cur')
            rebuilt = (root / 'new.cur').read_bytes()
            self.assertTrue(result['changed'])
            self.assertEqual(struct.unpack_from('<HHH', rebuilt), (0, 2, 2))
            self.assertEqual(cursor_frame(rebuilt, 1)[0], cursor_frame(blob, 1)[0])
            self.assertEqual(cursor_frame(rebuilt, 0)[1][4:6], (0, 1))
            self.assertEqual(cursor_frame(rebuilt, 1)[1][4:6], (1, 1))
            self.assertEqual(cursor_frame(rebuilt, 0)[2], rgba)
            self.assertEqual(original.read_bytes(), blob)
            # Bottom-up AND row: only the alpha=0 pixel is masked.
            self.assertEqual(cursor_frame(rebuilt, 0)[0][-8:], b'\0\0\0\0\x80\0\0\0')

    def test_cursor_unchanged_edit_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blob = fixture_cursor()
            (root / 'old.cur').write_bytes(blob)
            Image.frombytes('RGBA', (2, 2), bytes((10, 20, 30, 0)) * 4).save(root / 'same.png')
            result = self.native.rebuild_cursor(root / 'old.cur', {0: root / 'same.png'}, root / 'new.cur')
            self.assertFalse(result['changed'])
            self.assertEqual((root / 'new.cur').read_bytes(), blob)

    def test_cursor_rejects_unknown_frame_wrong_size_and_corrupt_offsets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'old.cur'
            original.write_bytes(fixture_cursor())
            Image.new('RGBA', (3, 2)).save(root / 'wrong.png')
            for edits, message in (({2: root / 'wrong.png'}, 'frame'), ({0: root / 'wrong.png'}, 'dimensions')):
                with self.subTest(edits=edits), self.assertRaisesRegex(self.native.NativeImageError, message):
                    self.native.rebuild_cursor(original, edits, root / 'new.cur')
                self.assertFalse((root / 'new.cur').exists())
            corrupt = bytearray(fixture_cursor())
            struct.pack_into('<I', corrupt, 18, 7)  # First payload points into the directory.
            original.write_bytes(corrupt)
            with self.assertRaises(self.native.NativeImageError):
                self.native.rebuild_cursor(original, {}, root / 'new.cur')
            self.assertFalse((root / 'new.cur').exists())

    def test_output_cannot_overwrite_source_or_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'old.cur'
            blob = fixture_cursor()
            original.write_bytes(blob)
            with self.assertRaises(self.native.NativeImageError):
                self.native.rebuild_cursor(original, {}, original)
            self.assertEqual(original.read_bytes(), blob)
            (root / 'existing.cur').write_bytes(b'keep')
            with self.assertRaises(self.native.NativeImageError):
                self.native.rebuild_cursor(original, {}, root / 'existing.cur')
            self.assertEqual((root / 'existing.cur').read_bytes(), b'keep')

    def test_jpeg_unchanged_copies_bytes_and_changed_uses_real_jpeg(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'original.jpg'
            Image.new('RGB', (16, 16), (60, 80, 100)).save(original, quality=89)
            before = original.read_bytes()
            with Image.open(original) as image:
                image.convert('RGBA').save(root / 'same.png')
            same = self.native.encode_jpeg(root / 'same.png', root / 'same.jpg', original)
            self.assertFalse(same['changed'])
            self.assertEqual((root / 'same.jpg').read_bytes(), before)
            Image.new('RGBA', (16, 16), (180, 50, 100, 255)).save(root / 'edit.png')
            changed = self.native.encode_jpeg(root / 'edit.png', root / 'changed.jpg', original)
            self.assertTrue(changed['changed'])
            self.assertTrue((root / 'changed.jpg').read_bytes().startswith(b'\xff\xd8'))
            self.assertLessEqual(changed['max_rgb_error'], 2)
            self.assertEqual(changed['alpha_changed_pixels'], 0)
            self.assertEqual(original.read_bytes(), before)

    def test_jpeg_rejects_resizing_and_new_transparency(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (2, 2)).save(root / 'old.jpg')
            for size, alpha, message in (((3, 2), 255, 'dimensions'), ((2, 2), 127, 'alpha')):
                Image.new('RGBA', size, (30, 40, 50, alpha)).save(root / 'edit.png')
                with self.assertRaisesRegex(self.native.NativeImageError, message):
                    self.native.encode_jpeg(root / 'edit.png', root / 'new.jpg', root / 'old.jpg')
                self.assertFalse((root / 'new.jpg').exists())

    def test_tlg_edit_roundtrip_checks_every_rgba_byte(self):
        if not (self.native.TOOLS / 'TlgLib.dll').is_file():
            self.skipTest('The local FreeMote 4.3.0 TLG library is required')
        compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('The .NET Framework C# compiler is required')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original, baseline = root / 'original.tlg', root / 'baseline.png'
            before = TLG5_2X2
            original.write_bytes(before)
            Image.frombytes('RGBA', (2, 2), TLG5_RGBA).save(baseline)
            decoded = self.native.read_rgba(original)
            self.assertEqual((decoded['width'], decoded['height']), (2, 2))
            self.assertEqual(decoded['rgba_sha256'], hashlib.sha256(TLG5_RGBA).hexdigest())
            same = self.native.encode_tlg(baseline, root / 'same.tlg', original)
            self.assertFalse(same['changed'])
            self.assertEqual((root / 'same.tlg').read_bytes(), before)
            forced = self.native.encode_tlg_many([{'png': baseline, 'target': root / 'forced.tlg',
                                                   'original': original, 'force': True}])[0]
            self.assertTrue(forced['encoded'])
            self.assertFalse(forced['changed'])
            self.assertEqual(self.native.read_rgba(root / 'forced.tlg')['rgba_sha256'], same['rgba_sha256'])
            with Image.open(baseline) as image:
                edit = image.convert('RGBA')
            edit.putpixel((0, 0), (12, 34, 56, 0))
            edit.putpixel((1, 0), (78, 90, 123, 127))
            edit.save(root / 'edit.png')
            expected = hashlib.sha256(edit.tobytes()).hexdigest()
            result = self.native.encode_tlg(root / 'edit.png', root / 'new.tlg', original)
            self.assertTrue(result['changed'])
            self.assertEqual(result['rgba_sha256'], expected)
            self.assertEqual(self.native.read_rgba(root / 'new.tlg')['rgba_sha256'], expected)
            self.assertEqual((root / 'new.tlg').read_bytes()[:6], before[:6])
            self.assertEqual(original.read_bytes(), before)
            with self.assertRaisesRegex(self.native.NativeImageError, 'PNG.*RGBA'):
                self.native._native('tlg', [{'png': str(root / 'edit.png'), 'target': str(root / 'mismatch.tlg'),
                                            'original': str(original), 'expected_rgba_sha256': '0' * 64}])
            self.assertFalse((root / 'mismatch.tlg').exists())
            Image.new('RGBA', (1, 1)).save(root / 'small.png')
            with self.assertRaisesRegex(self.native.NativeImageError, 'dimensions'):
                self.native.encode_tlg(root / 'small.png', root / 'wrong.tlg', original)
            self.assertFalse((root / 'wrong.tlg').exists())
            # Existing TLG0 metadata is part of the original shell and must survive.
            tail = b'tags' + struct.pack('<I', 3) + b'x=y'
            wrapped = b'TLG0.0\0sds\x1a' + struct.pack('<I', len(before)) + before + tail
            (root / 'wrapped.tlg').write_bytes(wrapped)
            self.native.encode_tlg(root / 'edit.png', root / 'wrapped_edit.tlg', root / 'wrapped.tlg')
            self.assertTrue((root / 'wrapped_edit.tlg').read_bytes().endswith(tail))
            self.assertEqual(self.native.read_rgba(root / 'wrapped_edit.tlg')['rgba_sha256'], expected)


if __name__ == '__main__':
    unittest.main()
