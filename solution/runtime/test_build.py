from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from solution.runtime import build as runtime


class RuntimeOutputTests(unittest.TestCase):
    def validate(self, path: Path) -> Path:
        self.assertTrue(hasattr(runtime, 'validate_output'), 'Missing strict runtime output validation')
        return runtime.validate_output(path)

    def test_only_designated_project_output_roots_are_accepted(self):
        for path in (runtime.TITLE / 'out/runtime_validation', runtime.ROOT / 'out', runtime.ROOT / 'work/test-output'):
            with self.subTest(path=path):
                self.assertEqual(self.validate(path), path.absolute())
        for path in (runtime.TITLE, runtime.ROOT, runtime.ROOT / 'src', runtime.TITLE / 'game_script',
                     runtime.TITLE / 'solution/patch/base/chs_patch', runtime.TITLE / 'game', runtime.TITLE / 'docs'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'output'):
                self.validate(path)

    def test_real_external_temporary_directory_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory(prefix='dc4-runtime-path-test-') as directory:
            path = Path(directory)
            with self.assertRaisesRegex(ValueError, 'output'):
                self.validate(path)
            self.assertEqual(list(path.iterdir()), [])

    def test_traversal_and_win32_alias_components_are_rejected(self):
        for path in (runtime.ROOT / 'out/../work/alias', runtime.ROOT / 'out/bad.', runtime.ROOT / 'out/bad ',
                     runtime.ROOT / 'out/CON.txt', runtime.ROOT / 'out/LPT¹'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.validate(path)

    def test_real_symlink_output_and_linked_existing_child_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='dc4-runtime-link-test-', dir=runtime.ROOT / 'out') as directory:
            root = Path(directory)
            target = root / 'target'
            target.mkdir()
            link = root / 'linked'
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f'Directory symlinks are unavailable: {error}')
            with self.assertRaisesRegex(ValueError, 'reparse|link'):
                self.validate(link / 'nested')
            child = target / 'dc4_fd_chs.dll'
            child.symlink_to(root / 'untouched-input')
            with self.assertRaisesRegex(ValueError, 'reparse|link'):
                self.validate(target)
            self.assertFalse((root / 'untouched-input').exists())


if __name__ == '__main__':
    unittest.main()
