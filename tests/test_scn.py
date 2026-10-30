from __future__ import annotations

import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path


TITLE_ROOT = Path(__file__).resolve().parents[1]
TOOL = TITLE_ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0'


class ScnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (TITLE_ROOT / 'game/data.xp3').exists() or not (TOOL / 'PsbDecompile.exe').exists():
            raise unittest.SkipTest('The project-local original game and FreeMote 4.3.0 are required')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.module_path = TITLE_ROOT / 'solution/script/scn.py'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def module(self):
        self.assertTrue(self.module_path.is_file(), 'Missing the DC4 SCN pipeline')
        return importlib.import_module('solution.script.scn')

    def corpus(self):
        scn = self.module()
        source = self.root / 'source'
        cache = self.root / 'json'
        if not source.exists():
            from solution.decrypt.cx import load_scheme
            from solution.unpack.xp3 import extract_entry, read_index
            archive = TITLE_ROOT / 'game/data.xp3'
            scheme = load_scheme()
            wanted = {'scn/4fd_asa191109a.ks.scn', 'scn/4fd_asa191109b.ks.scn', 'scn/4fd_com191125d.ks.scn'}
            source.mkdir()
            for entry in read_index(archive):
                if entry.name in wanted:
                    (source / Path(entry.name).name).write_bytes(extract_entry(archive, entry, scheme))
        scn.ensure_decompile(source, cache, TOOL)
        return scn, cache

    def test_real_scn_exports_all_visible_roles_and_protects_resource_keys(self):
        scn, cache = self.corpus()
        records = [r for p in scn.json_sources(cache) for r in scn.collect_records(scn.load_json(p), p.name)]
        self.assertTrue({'body', 'speaker', 'title', 'autosave', 'choice', 'message', 'image_alt'} <= {r.kind for r in records})
        self.assertTrue(any(r.source == '有里栖、お腹空いてない？' for r in records))
        self.assertFalse(any(r.source.endswith('.stand') or r.source.startswith('comu有里') for r in records))

    def test_translation_guard_rejects_duplicate_missing_stale_and_changed_control(self):
        scn, cache = self.corpus()
        doc = scn.load_json(cache / '4fd_asa191109a.ks.json')
        records = scn.collect_records(doc, '4fd_asa191109a.ks.json')
        text = scn.format_records(records)
        with self.assertRaisesRegex(scn.ScriptError, 'Duplicate'):
            scn.parse_translation(text + text, records)
        with self.assertRaisesRegex(scn.ScriptError, 'Missing'):
            scn.parse_translation('', records)
        with self.assertRaisesRegex(scn.ScriptError, 'Source mismatch'):
            scn.parse_translation(text.replace('「じゃあいっくん、今日も修行、張り切っていこうか」', 'stale', 1), records)
        item = next(r for r in records if r.kind == 'body' and '\\n' in r.source)
        values = {r.id: r.source for r in records}
        values[item.id] = item.source.replace('\\n', '')
        with self.assertRaisesRegex(scn.ScriptError, 'Control'):
            scn.apply_translations(copy.deepcopy(doc), records, values)

    def test_real_scn_rejects_stale_visible_length_cache(self):
        scn, cache = self.corpus()
        doc = scn.load_json(cache / '4fd_asa191109a.ks.json')
        doc['scenes'][0]['texts'][0][1][0][2] += 1
        with self.assertRaisesRegex(scn.ScriptError, 'Derived text cache'):
            scn.collect_records(doc, '4fd_asa191109a.ks.json')

    def test_speaker_translation_preserves_logical_names_and_uses_display_override(self):
        scn, cache = self.corpus()
        doc = scn.load_json(cache / '4fd_asa191109a.ks.json')
        records = scn.collect_records(doc, '4fd_asa191109a.ks.json')
        values = {r.id: ('测试人名' if r.kind == 'speaker' and r.source == '有里咲' else r.source) for r in records}
        before_lines = copy.deepcopy(doc['scenes'][0]['lines'])
        scn.apply_translations(doc, records, values)
        text = doc['scenes'][0]['texts'][0]
        self.assertEqual(text[0], '有里咲')
        self.assertEqual(text[1][0][0], '测试人名')
        self.assertEqual(text[2][0]['name'], '有里咲')
        self.assertEqual(doc['scenes'][0]['lines'], before_lines)

    def test_phone_translation_synchronizes_body_and_all_saved_message_states(self):
        scn, cache = self.corpus()
        doc = scn.load_json(cache / '4fd_asa191109b.ks.json')
        records = scn.collect_records(doc, '4fd_asa191109b.ks.json')
        phone = next(t for s in doc['scenes'] for t in s.get('texts', []) if str(t[0]).startswith('comu') and not t[1][0][1].startswith('%'))
        source = phone[1][0][1]
        message = next(r for r in records if r.kind == 'message' and r.source == source)
        values = {r.id: ('中文电话消息。' if r.id == message.id else r.source) for r in records}
        scn.apply_translations(doc, records, values)
        self.assertEqual(phone[1][0][1:3], ['中文电话消息。', 7])
        states = [v for _, v in scn._walk(doc) if isinstance(v, dict) and 'comumode' in v]
        self.assertTrue(states)
        self.assertFalse(any(m.get('text') == source for v in states for m in v['comumode']))

    def test_reexport_preserves_existing_translation_bytes(self):
        scn, cache = self.corpus()
        target = self.root / 'preserve'
        raw, translated = target / 'raw', target / 'translated'
        scn.export_text(cache, raw, translated)
        p = translated / '4fd_asa191109a.ks.txt'
        # Change the translation line only, keeping the original-source guard intact.
        lines = p.read_text('utf-8').splitlines()
        i = next(i for i, line in enumerate(lines) if line.startswith('●') and 'じゃあいっくん' in line)
        lines[i] = lines[i].split('●', 2)[0] + '●' + lines[i].split('●', 2)[1] + '●' + json.dumps('「中文显示与回填测试。」', ensure_ascii=False)
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        preserved = p.read_bytes()
        scn.export_text(cache, raw, translated)
        self.assertEqual(p.read_bytes(), preserved)

    def test_actual_scn_original_and_chinese_recompile_then_decompile(self):
        scn, cache = self.corpus()
        base = self.root / 'roundtrip'
        raw, translated = base / 'raw', base / 'translated'
        scn.export_text(cache, raw, translated)
        original = scn.compile_translations(cache, translated, base / 'original', TOOL, verify=True)
        self.assertEqual(original['files'], 3)
        self.assertEqual(original['verified'], 3)
        self.assertEqual(original['changed_records'], 0)
        path = translated / '4fd_asa191109a.ks.txt'
        lines = path.read_text('utf-8').splitlines()
        i = next(i for i, line in enumerate(lines) if line.startswith('●') and 'じゃあいっくん' in line)
        prefix = '●' + lines[i].split('●', 2)[1] + '●'
        lines[i] = prefix + json.dumps('「中文脚本回填验证，句子长度变化也能重新读取。」', ensure_ascii=False)
        i = next(i for i, line in enumerate(lines) if line.startswith('●') and 'すっかり恒例' in line)
        lines[i] = '●' + lines[i].split('●', 2)[1] + '●' + json.dumps('　这是每天的基础练习。', ensure_ascii=False)
        i = next(i for i, line in enumerate(lines) if line.startswith('●') and line.endswith('"有里咲"'))
        lines[i] = '●' + lines[i].split('●', 2)[1] + '●' + json.dumps('测试人名', ensure_ascii=False)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        path = translated / '4fd_com191125d.ks.txt'
        lines = path.read_text('utf-8').splitlines()
        i = next(i for i, line in enumerate(lines) if line.startswith('●') and '有里栖、お腹空いてない？' in line)
        lines[i] = '●' + lines[i].split('●', 2)[1] + '●' + json.dumps('中文选择项测试？', ensure_ascii=False)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        chinese = scn.compile_translations(cache, translated, base / 'chinese', TOOL, verify=True)
        self.assertEqual(chinese['changed_records'], 4)
        self.assertEqual(chinese['verified'], 3)
        verify = Path(chinese['verification_json'])
        rebuilt = scn.load_json(verify / '4fd_asa191109a.ks.json')
        self.assertEqual(rebuilt['scenes'][0]['texts'][0][1][0][1], '「中文脚本回填验证，句子长度变化也能重新读取。」')
        self.assertEqual(rebuilt['scenes'][0]['texts'][0][1][0][2], 24)
        self.assertEqual(rebuilt['scenes'][0]['texts'][0][2][0]['name'], '有里咲')
        self.assertEqual(rebuilt['scenes'][0]['texts'][0][0], '有里咲')
        self.assertEqual(rebuilt['scenes'][0]['texts'][0][1][0][0], '测试人名')
        ruby = rebuilt['scenes'][0]['texts'][5][1][0]
        self.assertEqual(ruby[1:], ['　这是每天的基础练习。', 11, '　这是每天的基础练习。', '　这是每天的基础练习。'])
        choices = scn.load_json(verify / '4fd_com191125d.ks.json')['scenes'][1]['selects']
        self.assertEqual(choices[0]['text'], '中文选择项测试？')
        self.assertEqual(choices[0]['target'], '*sel1-1')


if __name__ == '__main__':
    unittest.main()
