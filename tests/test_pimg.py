from __future__ import annotations

import importlib
import json
from pathlib import Path
import unittest
import uuid

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0'
SOURCE = ROOT / 'solution/unpack/work/data'


class PimgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = (SOURCE / 'uipsd/indicator.pimg', TOOLS / 'PsbDecompile.exe', TOOLS / 'PsBuild.exe')
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise unittest.SkipTest('Local original PIMG and FreeMote 4.3.0 are required: ' + ', '.join(missing))
        cls.work = ROOT / 'solution/patch/work' / ('tests-' + uuid.uuid4().hex[:12])
        cls.work.mkdir(parents=True)

    def module(self):
        self.assertTrue((ROOT / 'solution/patch/images.py').is_file(), 'Missing the PIMG image workflow')
        return importlib.import_module('solution.patch.images')

    def asset(self, label):
        module = self.module()
        target = self.work / label
        module.export_images(SOURCE, target, TOOLS, ['uipsd/indicator.pimg'])
        return module, target / 'uipsd/indicator.pimg'

    def test_existing_image_edits_survive_export_and_force_is_backed_up(self):
        images, asset = self.asset('preserve')
        png = asset / 'indicator/3.png'
        with Image.open(png) as source:
            image = source.convert('RGBA')
        image.putpixel((0, 0), (21, 42, 63, 127))
        image.save(png)
        edited = png.read_bytes()
        images.export_images(SOURCE, asset.parents[1], TOOLS, ['uipsd/indicator.pimg'])
        self.assertEqual(png.read_bytes(), edited)
        result = images.export_images(SOURCE, asset.parents[1], TOOLS, ['uipsd/indicator.pimg'], force=True)
        backup = Path(result['files'][0]['backup'])
        self.assertEqual((backup / 'indicator/3.png').read_bytes(), edited)
        self.assertNotEqual(png.read_bytes(), edited)

    def test_snapshot_rejects_external_resource_paths(self):
        images, asset = self.asset('external')
        meta = asset / 'indicator.resx.json'
        value = json.loads(meta.read_text('utf-8-sig'))
        value['Resources']['3'] = '../outside.png'
        meta.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(images.ImageError, 'resource path'):
            images.snapshot(asset)

    def test_comparison_checks_alpha_layer_coordinates_and_resource_references(self):
        images, asset = self.asset('semantics')
        before = images.snapshot(asset)
        png = asset / 'indicator/3.png'
        with Image.open(png) as source:
            image = source.convert('RGBA')
        old = image.getpixel((0, 0))
        image.putpixel((0, 0), (*old[:3], (old[3] + 1) % 256))
        image.save(png)
        with self.assertRaisesRegex(images.ImageError, 'pixels'):
            images.compare_snapshots(before, images.snapshot(asset))
        image.putpixel((0, 0), old)
        image.save(png)
        docpath = asset / 'indicator.json'
        doc = json.loads(docpath.read_text('utf-8-sig'))
        doc['layers'][0]['left'] += 1
        docpath.write_text(json.dumps(doc), encoding='utf-8')
        with self.assertRaisesRegex(images.ImageError, 'structure'):
            images.compare_snapshots(before, images.snapshot(asset))
        doc['layers'][0]['left'] -= 1
        doc['3.tlg'] = doc['4.tlg']
        docpath.write_text(json.dumps(doc), encoding='utf-8')
        with self.assertRaisesRegex(images.ImageError, 'resource'):
            images.compare_snapshots(before, images.snapshot(asset))

    def test_real_pimg_roundtrip_and_pixel_edit_preserve_alpha_and_coordinates(self):
        images, asset = self.asset('build')
        plain = images.build_images(asset.parents[1], self.work / 'plain', TOOLS, ['uipsd/indicator.pimg'])
        self.assertEqual(plain['verified'], 1)
        self.assertEqual(plain['changed'], 0)
        png = asset / 'indicator/3.png'
        with Image.open(png) as source:
            image = source.convert('RGBA')
        image.putpixel((0, 0), (17, 34, 51, 119))
        image.save(png)
        edited = images.build_images(asset.parents[1], self.work / 'edited', TOOLS, ['uipsd/indicator.pimg'])
        self.assertEqual(edited['verified'], 1)
        self.assertEqual(edited['changed'], 1)
        checked = Path(edited['files'][0]['verified_asset'])
        with Image.open(checked / 'indicator/3.png') as actual:
            self.assertEqual(actual.convert('RGBA').getpixel((0, 0)), (17, 34, 51, 119))
        expected_layers = json.loads((asset / 'indicator.json').read_text('utf-8-sig'))['layers']
        actual_layers = json.loads((checked / 'indicator.json').read_text('utf-8-sig'))['layers']
        self.assertEqual(actual_layers, expected_layers)

    def test_publish_requires_explicit_selection_and_only_adds_changed_files(self):
        images, asset = self.asset('publish')
        static, manifest = self.work / 'static', self.work / 'static_manifest.txt'
        with self.assertRaisesRegex(images.ImageError, 'explicit'):
            images.publish_images(asset.parents[1], TOOLS, [], static_root=static, manifest=manifest)
        unchanged = images.publish_images(asset.parents[1], TOOLS, ['uipsd/indicator.pimg'], static_root=static, manifest=manifest)
        self.assertEqual(unchanged['published'], 0)
        self.assertFalse((static / 'uipsd/indicator.pimg').exists())
        png = asset / 'indicator/3.png'
        with Image.open(png) as source:
            image = source.convert('RGBA')
        image.putpixel((0, 0), (19, 38, 57, 117))
        image.save(png)
        changed = images.publish_images(asset.parents[1], TOOLS, ['uipsd/indicator.pimg'], static_root=static, manifest=manifest)
        self.assertEqual(changed['published'], 1)
        self.assertEqual(manifest.read_text('utf-8').splitlines(), ['uipsd/indicator.pimg'])
        self.assertTrue((static / 'uipsd/indicator.pimg').is_file())

    def test_publish_rejects_maintained_source_destinations(self):
        images, asset = self.asset('publish_guard')
        with self.assertRaisesRegex(images.ImageError, 'publication output'):
            images.publish_images(asset.parents[1], TOOLS, ['uipsd/indicator.pimg'],
                static_root=ROOT / 'solution/patch/invalid_static',
                manifest=ROOT / 'solution/patch/invalid_manifest.txt')


if __name__ == '__main__':
    unittest.main()
