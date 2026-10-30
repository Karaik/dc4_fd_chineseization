from pathlib import Path
import tempfile
import unittest

from solution.build.build import add_scn_overrides, runtime_assets


class BuildInputTests(unittest.TestCase):
    def test_static_script_collision_does_not_replace_the_static_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'compiled'
            source.mkdir()
            (source / 'same.scn').write_bytes(b'compiled')
            patch = root / 'patch'
            (patch / 'scn').mkdir(parents=True)
            target = patch / 'scn/same.scn'
            target.write_bytes(b'explicit static')
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                add_scn_overrides([str(source / 'same.scn')], source, patch)
            self.assertEqual(target.read_bytes(), b'explicit static')

    def test_only_an_explicit_maintained_font_is_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / 'assets'
            font = assets / 'chs_patch/fonts/example.otf'
            font.parent.mkdir(parents=True)
            font.write_bytes(b'font-copy-fixture')
            (assets / 'unselected.otf').write_bytes(b'not selected')
            package = root / 'package'
            selected = runtime_assets(package, '[runtime]\nfontpath=chs_patch/fonts/example.otf\n', assets)
            self.assertEqual(selected, ['chs_patch/fonts/example.otf'])
            self.assertEqual((package / selected[0]).read_bytes(), font.read_bytes())
            self.assertFalse((package / 'unselected.otf').exists())

    def test_empty_fontpath_needs_no_assets_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / 'package'
            self.assertEqual(runtime_assets(package, '[runtime]\nfontpath=\n'), [])
            self.assertFalse(package.exists())

    def test_font_cannot_escape_or_replace_an_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ('../font.otf', '/font.otf', 'file.exe', 'font.otf.'):
                with self.assertRaises(ValueError):
                    runtime_assets(root / 'package', '[runtime]\nfontpath=' + path + '\n', root / 'assets')


if __name__ == '__main__':
    unittest.main()
