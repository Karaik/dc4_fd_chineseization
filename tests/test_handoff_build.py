from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from solution.build import build


class HandoffBuildTests(unittest.TestCase):
    def test_explicit_candidate_static_inputs_are_packed_without_scanning_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'candidate/static'
            base.mkdir(parents=True)
            (base / 'selected.png').write_bytes(b'selected')
            (base / 'unlisted.png').write_bytes(b'not selected')
            manifest = root / 'candidate/static_manifest.txt'
            manifest.write_text('selected.png\n')
            target = root / 'patch'
            target.mkdir()
            with patch.object(build, 'ROOT', root):
                result = build.static_resources(target, base, manifest)
            self.assertEqual(result, ['selected.png'])
            self.assertEqual((target / 'selected.png').read_bytes(), b'selected')
            self.assertFalse((target / 'unlisted.png').exists())

    def test_candidate_static_manifest_cannot_escape_the_static_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'candidate/static'
            base.mkdir(parents=True)
            (root / 'outside.png').write_bytes(b'outside')
            manifest = root / 'candidate/static_manifest.txt'
            manifest.write_text('../../outside.png\n')
            target = root / 'patch'
            target.mkdir()
            with patch.object(build, 'ROOT', root), self.assertRaises(ValueError):
                build.static_resources(target, base, manifest)
            self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
