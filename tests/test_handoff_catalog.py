from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import struct
from pathlib import Path
import tempfile
import unittest
import zipfile
import warnings

from PIL import Image

from solution.patch import handoff_catalog as catalog


def digest(data):
    return hashlib.sha256(data).hexdigest()


class HandoffCatalogTests(unittest.TestCase):
    def fixture(self, root):
        png = io.BytesIO()
        Image.new('RGBA', (2, 3), (1, 2, 3, 127)).save(png, format='PNG')
        entries = []
        for ordinal, (resource, data) in enumerate((('image/a.png', png.getvalue()), ('scn/test.ks.scn', b'original scn'))):
            row = {'archive': 'data', 'ordinal': ordinal, 'status': 'resolved', 'package_path': resource,
                   'raw_path': f'raw/data/{ordinal:05}.bin', 'sha256': digest(data), 'bytes': len(data)}
            entries.append(row)
            for relative in ('named/data/' + resource, row['raw_path']):
                path = root / 'solution/unpack/work' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        self.write(root / 'solution/unpack/resource_names.json', {'project': 'dc5_to4_chineseization', 'entries': entries})
        self.write(root / 'solution/unpack/work/manifests/data.json', {'project': 'dc5_to4_chineseization', 'entries': entries})
        script = root / 'game_script/translated_script/scn/test.ks.txt'
        script.parent.mkdir(parents=True)
        script.write_text('source\ntranslation\n', encoding='utf-8')
        self.write(root / 'game_script/raw_script/system/system.json', {'source_files': {}})
        self.write(root / 'game_script/translated_script/system/system.json', {'source_files': {}})
        return {'图片/data/image/a.png': png.getvalue(), '文本/scn/test.ks.txt': script.read_bytes(),
                '文本/system/system.json': (root / 'game_script/translated_script/system/system.json').read_bytes(),
                catalog.NOTE_NAME: '《D.C.5 to 4 Luminous Memories》\n规则'.encode('utf-8')}

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def archive(self, root, files):
        path = root / 'delivery.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return path

    def original(self, root, resource, payload):
        map_path = root / 'solution/unpack/resource_names.json'
        report = json.loads(map_path.read_text('utf-8'))
        ordinal = len(report['entries'])
        row = {'archive': 'data', 'ordinal': ordinal, 'status': 'resolved', 'package_path': resource,
               'raw_path': f'raw/data/{ordinal:05}.bin', 'sha256': digest(payload), 'bytes': len(payload)}
        report['entries'].append(row)
        self.write(map_path, report)
        self.write(root / 'solution/unpack/work/manifests/data.json', report)
        for relative in ('named/data/' + resource, row['raw_path']):
            path = root / 'solution/unpack/work' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def pimg(self, root, files):
        from solution.patch import images
        asset = root / 'solution/patch/work/images/uipsd/test.pimg'
        self.write(asset / 'test.json', {'0.tlg': '#resource#0', 'layers': []})
        self.write(asset / 'test.resx.json', {'PsbType': 'Pimg', 'PsbVersion': 3, 'Resources': {'0': 'test/0.png'}})
        png = asset / 'test/0.png'
        png.parent.mkdir()
        png.write_bytes(files['图片/data/image/a.png'])
        def array(values):
            count_width = max(1, (len(values).bit_length() + 7) // 8)
            width = max(1, (max(values, default=0).bit_length() + 7) // 8)
            return bytes((12 + count_width,)) + len(values).to_bytes(count_width, 'little') + bytes((12 + width,)) + b''.join(value.to_bytes(width, 'little') for value in values)

        alphabet, parents, indexes, edges = [0], [0], [], {}
        def child(parent, character):
            if not alphabet[parent]:
                alphabet[parent] = len(parents)
                alphabet.extend([0] * 256)
                parents.extend([0] * 256)
            key = parent, character
            if key not in edges:
                edges[key] = alphabet[parent] + character
                parents[edges[key]] = parent
            return edges[key]
        for name in ('0.tlg', 'layers'):
            parent = 0
            for character in name.encode('utf-8'):
                parent = child(parent, character)
            indexes.append(child(parent, 0))
        names = array(alphabet) + array(parents) + array(indexes)
        strings_at = 44 + len(names)
        strings = array([])
        root_at = strings_at + len(strings)
        obj = b'\x21' + array([0, 1]) + array([0, 2]) + b'\x19\0\x20' + array([])
        offsets_at = root_at + len(obj)
        lengths_at = offsets_at + 4
        resource_at = lengths_at + 4
        source = bytearray(44)
        source[:8] = b'PSB\0\x03\0\0\0'
        struct.pack_into('<3I', source, 12, 44, strings_at, root_at)
        struct.pack_into('<4I', source, 24, offsets_at, lengths_at, resource_at, root_at)
        payload = bytes(source) + names + strings + obj + array([0]) + array([len(png.read_bytes())]) + png.read_bytes()
        self.original(root, 'uipsd/test.pimg', payload)
        self.write(asset / images.METADATA, {'source_path': 'uipsd/test.pimg', 'source_sha256': digest(payload),
                   'shell': {'type': 'none'}, 'baseline': images.snapshot(asset)})
        files['图片/data/uipsd/test.pimg/test/0.png'] = png.read_bytes()
        return asset

    def test_binds_original_resource_without_archive_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = self.archive(root, self.fixture(root))
            value = catalog.build_catalog(package, b'password', root=root)
            row = value['files']['图片/data/image/a.png']
            self.assertEqual(row['resource'], 'image/a.png')
            self.assertEqual(row['archive'], 'data')
            self.assertEqual(row['source']['path'], 'solution/unpack/work/named/data/image/a.png')
            self.assertEqual((row['width'], row['height']), (2, 3))
            self.assertEqual(row['baseline_sha256'], row['source']['sha256'])
            catalog.validate_catalog(value, root)
            written = catalog.write_catalog(package, b'password', root / 'catalog.json', root=root)
            self.assertEqual(json.loads((root / 'catalog.json').read_text('utf-8')), written)

    def test_rejects_payload_with_wrong_pixels_and_missing_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = self.fixture(root)
            bad = io.BytesIO()
            Image.new('RGBA', (2, 3), (1, 2, 3, 126)).save(bad, format='PNG')
            files['图片/data/image/a.png'] = bad.getvalue()
            with self.assertRaisesRegex(ValueError, 'baseline|source|pixel'):
                catalog.build_catalog(self.archive(root, files), b'', root=root)
            del files['图片/data/image/a.png']
            with self.assertRaisesRegex(ValueError, 'missing|coverage'):
                catalog.build_catalog(self.archive(root, files), b'', root=root)

    def test_rejects_unsafe_unknown_and_case_colliding_members(self):
        for extra in ('../escape.png', '图片/data/image/A.png', '图片/data/image/.log', '图片/data/image/a.png/extra', '图片/data/image/a.png:stream'):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                files = self.fixture(root)
                files[extra] = b'x'
                with self.assertRaises(ValueError):
                    catalog.build_catalog(self.archive(root, files), b'', root=root)

    def test_rejects_duplicate_and_symlink_zip_members(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                package = self.archive(root, self.fixture(root))
                with zipfile.ZipFile(package, 'a') as archive:
                    info = zipfile.ZipInfo('link' if symlink else catalog.NOTE_NAME)
                    if symlink:
                        info.create_system = 3
                        info.external_attr = 0o120777 << 16
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        archive.writestr(info, b'target')
                with self.assertRaises(ValueError):
                    catalog.build_catalog(package, b'', root=root)

    def test_stale_source_and_tampered_catalog_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = catalog.build_catalog(self.archive(root, self.fixture(root)), b'', root=root)
            bad = copy.deepcopy(value)
            bad['files']['图片/data/image/a.png']['resource'] = 'data/image/a.png'
            with self.assertRaises(ValueError):
                catalog.validate_catalog(bad, root)
            bad = copy.deepcopy(value)
            bad['files']['图片/data/image/a.png']['rgba_sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                catalog.validate_catalog(bad, root)
            source = root / value['files']['图片/data/image/a.png']['source']['path']
            source.write_bytes(b'stale')
            with self.assertRaisesRegex(ValueError, 'SHA|sha|source'):
                catalog.validate_catalog(value, root)

    def test_pimg_binds_container_resource_key_and_retained_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = self.fixture(root)
            asset = self.pimg(root, files)
            value = catalog.build_catalog(self.archive(root, files), b'', root=root)
            row = value['files']['图片/data/uipsd/test.pimg/test/0.png']
            self.assertEqual(row['kind'], 'pimg')
            self.assertEqual(row['source']['path'], 'solution/unpack/work/named/data/uipsd/test.pimg')
            self.assertEqual(row['pimg']['resource_key'], '0.tlg')
            self.assertEqual(row['pimg']['resource_index'], '#resource#0')
            self.write(asset / 'test.json', {'0.tlg': '#resource#0', 'layers': [{'left': 1}]})
            with self.assertRaisesRegex(ValueError, 'structure'):
                catalog.validate_catalog(value, root)

    def test_pimg_rejects_retained_resource_key_claim_not_in_original_container(self):
        with tempfile.TemporaryDirectory() as folder:
            from solution.patch import images
            root = Path(folder)
            files = self.fixture(root)
            asset = self.pimg(root, files)
            self.write(asset / 'test.json', {'0.tlg': '#resource#1', 'layers': []})
            meta = json.loads((asset / images.METADATA).read_text('utf-8'))
            meta['baseline'] = images.snapshot(asset)
            self.write(asset / images.METADATA, meta)
            with self.assertRaisesRegex(ValueError, 'indices'):
                catalog.build_catalog(self.archive(root, files), b'', root=root)

    def test_cursor_binds_every_original_frame_and_hotspot(self):
        from tests.test_native_images import fixture_cursor
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = self.fixture(root)
            self.original(root, 'image/sys/test.cur', fixture_cursor())
            for index, color in enumerate(((10, 20, 30, 0), (70, 80, 90, 255))):
                png = io.BytesIO()
                Image.new('RGBA', (2, 2), color).save(png, format='PNG')
                files[f'图片/data/image/sys/test.cur/frame_{index:02}_2x2.png'] = png.getvalue()
            value = catalog.build_catalog(self.archive(root, files), b'', root=root)
            self.assertEqual(value['counts']['cursor'], 2)
            row = value['files']['图片/data/image/sys/test.cur/frame_01_2x2.png']
            self.assertEqual((row['frame_index'], row['hotspot_x'], row['hotspot_y']), (1, 1, 1))
            del files['图片/data/image/sys/test.cur/frame_00_2x2.png']
            with self.assertRaisesRegex(ValueError, 'coverage'):
                catalog.build_catalog(self.archive(root, files), b'', root=root)

    def test_pimg_rejects_relabelled_internal_key_even_if_cache_claims_it(self):
        from solution.patch import images
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = self.fixture(root)
            asset = self.pimg(root, files)
            self.write(asset / 'test.json', {'1.tlg': '#resource#0', 'layers': []})
            self.write(asset / 'test.resx.json', {'PsbType': 'Pimg', 'PsbVersion': 3, 'Resources': {'1': 'test/0.png'}})
            meta = json.loads((asset / images.METADATA).read_text('utf-8'))
            meta['baseline'] = images.snapshot(asset)
            self.write(asset / images.METADATA, meta)
            with self.assertRaisesRegex(ValueError, 'structure|keys'):
                catalog.build_catalog(self.archive(root, files), b'', root=root)

    def test_rejects_hidden_windows_zip_attribute(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = self.fixture(root)
            package = root / 'delivery.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                for name, data in files.items():
                    info = zipfile.ZipInfo(name)
                    info.create_system = 0
                    info.external_attr = 2 if name == catalog.NOTE_NAME else 32
                    archive.writestr(info, data)
            with self.assertRaisesRegex(ValueError, 'hidden'):
                catalog.build_catalog(package, b'', root=root)

    def test_output_cannot_overwrite_package_or_original_source(self):
        for relative in ('delivery.zip', 'solution/unpack/work/named/data/image/a.png'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                package = self.archive(root, self.fixture(root))
                target = root / relative
                before = target.read_bytes()
                with self.assertRaisesRegex(ValueError, 'overwrite|input'):
                    catalog.write_catalog(package, b'', target, root=root)
                self.assertEqual(target.read_bytes(), before)

    def test_catalog_remains_valid_when_whole_project_is_moved(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / 'original'
            original.mkdir()
            value = catalog.build_catalog(self.archive(original, self.fixture(original)), b'', root=original)
            self.assertEqual(value['package']['path'], 'delivery.zip')
            moved = Path(folder) / 'moved'
            shutil.copytree(original, moved)
            catalog.validate_catalog(value, moved)


if __name__ == '__main__':
    unittest.main()
