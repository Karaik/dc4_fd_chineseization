import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

try:
    handoff = importlib.import_module('solution.patch.handoff_return')
except ImportError:
    handoff = None


def png(size=(3, 2), color=(11, 22, 33, 128), optimize=False):
    stream = io.BytesIO()
    Image.new('RGBA', size, color).save(stream, format='PNG', optimize=optimize)
    return stream.getvalue()


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(handoff, 'The return-to-patch importer is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = '图片/bgimage/background.png'
        self.catalog = {'files': {self.name: {'kind': 'png', 'width': 3, 'height': 2}}}

    def test_partial_directory_keeps_original_path_and_rejects_unknown_files(self):
        file = self.root / self.name
        file.parent.mkdir(parents=True)
        file.write_bytes(png())
        returned = handoff.collect_return_files(self.root, self.catalog)
        self.assertEqual(returned, {self.name: png()})
        (self.root / '.gitkeep').write_text('')
        with self.assertRaisesRegex(ValueError, 'Unknown|hidden'):
            handoff.collect_return_files(self.root, self.catalog)

    def test_zip_rejects_traversal_and_duplicate_case_before_import(self):
        path = self.root / 'returned.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('../outside.png', png())
        with self.assertRaises(ValueError):
            handoff.collect_return_files(path, self.catalog)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(self.name, png())
            z.writestr(self.name.upper(), png())
        with self.assertRaisesRegex(ValueError, 'Duplicate|duplicate'):
            handoff.collect_return_files(path, self.catalog)

    def test_png_reencoding_is_unchanged_but_pixels_and_size_are_checked(self):
        baseline = handoff.png_info(png())
        row = {'width': 3, 'height': 2, 'rgba_sha256': baseline['rgba_sha256']}
        self.assertFalse(handoff.image_changed(png(optimize=True), row, self.name))
        self.assertTrue(handoff.image_changed(png(color=(44, 55, 66, 128)), row, self.name))
        with self.assertRaisesRegex(ValueError, 'dimensions|size'):
            handoff.image_changed(png(size=(4, 2)), row, self.name)

    def test_changed_disabled_system_text_requires_program_review(self):
        doc = {'documents': {'dialog.ks': {'enabled': False, 'source': '日本語', 'translation': '中文'}},
               'bytecode': {'menu.tjs': {'strings': [
                   {'index': 0, 'enabled': False, 'source': 'ファイル', 'translation': '文件'},
                   {'index': 1, 'enabled': False, 'source': 'internal', 'translation': 'internal'}]}}}
        saved = copy.deepcopy(doc)
        with self.assertRaisesRegex(ValueError, 'review'):
            handoff.review_system_edits(doc, reviewed=False)
        result, selected = handoff.review_system_edits(doc, reviewed=True)
        self.assertEqual(doc, saved)
        self.assertEqual(len(selected), 2)
        self.assertTrue(result['documents']['dialog.ks']['enabled'])
        self.assertTrue(result['bytecode']['menu.tjs']['strings'][0]['enabled'])
        self.assertFalse(result['bytecode']['menu.tjs']['strings'][1]['enabled'])

    def test_existing_static_override_requires_explicit_replacement(self):
        static = self.root / 'static'
        static.mkdir()
        records = {}
        handoff.add_static(static, 'uipsd/window.pimg', b'first', records)
        with self.assertRaisesRegex(ValueError, 'conflict|Conflict'):
            handoff.add_static(static, 'uipsd/window.pimg', b'second', records)
        handoff.add_static(static, 'uipsd/window.pimg', b'second', records, replace=True)
        self.assertEqual((static / 'uipsd/window.pimg').read_bytes(), b'second')

    def test_build_inputs_cannot_escape_candidate_or_accept_modified_files(self):
        stage = self.root / 'solution/patch/work/return_01'
        (stage / 'translated/scn').mkdir(parents=True)
        (stage / 'translated/system').mkdir()
        (stage / 'translated/system/system.json').write_text('{}')
        (stage / 'static').mkdir()
        (stage / 'static_manifest.txt').write_text('')
        summary = {'schema_version': 1, 'project': self.root.name,
                   'inputs': {'translation_dir': 'translated/scn', 'system_translation': 'translated/system/system.json',
                              'static_root': 'static', 'static_manifest': 'static_manifest.txt'},
                   'input_files': {'translated/system/system.json': hashlib.sha256(b'{}').hexdigest(),
                                   'static_manifest.txt': hashlib.sha256(b'').hexdigest()}}
        (stage / 'handoff_summary.json').write_text(json.dumps(summary))
        self.assertEqual(handoff.build_inputs(stage, root=self.root)['static_root'], stage / 'static')
        summary['inputs']['static_root'] = '../../outside'
        (stage / 'handoff_summary.json').write_text(json.dumps(summary))
        with self.assertRaises(ValueError):
            handoff.build_inputs(stage, root=self.root)
        summary['inputs']['static_root'] = 'static'
        (stage / 'handoff_summary.json').write_text(json.dumps(summary))
        (stage / 'translated/system/system.json').write_text('{"modified":true}')
        with self.assertRaisesRegex(ValueError, 'modified'):
            handoff.build_inputs(stage, root=self.root)

    def test_dotdot_cannot_claim_a_formal_directory_as_work_output(self):
        work = self.root / 'solution/patch/work'
        protected = self.root / 'solution/patch/base/chs_patch'
        work.mkdir(parents=True)
        protected.mkdir(parents=True)
        escaped = work / '../base/chs_patch'
        with self.assertRaises(ValueError):
            handoff._plain(escaped, work)


if __name__ == '__main__':
    unittest.main()
