from __future__ import annotations

import copy
import struct
import unittest
from pathlib import Path

from solution.script import system_text
from solution.unpack.system_inventory import bytecode_strings, decode_text


class SystemTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document, cls.plans = system_text.collect()

    def require_expanded_schema(self):
        self.assertEqual(self.document["schema_version"], 2, "System editor must cover all original carriers")

    def test_all_original_carriers_roundtrip_byte_identically(self):
        self.require_expanded_schema()
        self.assertEqual(len(self.document["source_files"]), 338)
        self.assertEqual(len(self.document["bytecode"]), 207)
        self.assertEqual(len(self.document["documents"]), 131)
        outputs, changed = system_text.rebuild(self.document)
        self.assertEqual(changed, 0)
        for name, data in outputs.items():
            with self.subTest(name=name):
                self.assertEqual(data, (system_text.SOURCE_ROOT / name).read_bytes())

    def test_variable_length_chinese_constants_preserve_objects_and_indices(self):
        self.require_expanded_schema()
        document = copy.deepcopy(self.document)
        cases = {
            "sysscn/nocfchkdialog.tjs": (21, "确认返回标题画面吗？这是变长的系统确认文字。"),
            "sysscn/advancedmenu.tjs": (37, "高级设置与显示选项(&A)"),
        }
        for name, (index, translation) in cases.items():
            row = document["bytecode"][name]["strings"][index]
            row.update(enabled=True, translation=translation)
        outputs, changed = system_text.rebuild(document)
        self.assertEqual(changed, 2)
        for name, (index, translation) in cases.items():
            old = (system_text.SOURCE_ROOT / name).read_bytes()
            new = outputs[name]
            old_end = 12 + struct.unpack_from("<I", old, 16)[0]
            new_end = 12 + struct.unpack_from("<I", new, 16)[0]
            self.assertEqual(old[old_end:], new[new_end:])
            before = bytecode_strings(old)
            after = bytecode_strings(new)
            self.assertEqual(len(after), len(before))
            self.assertEqual(after[index], translation)
            self.assertEqual(before[:index] + before[index + 1 :], after[:index] + after[index + 1 :])
            self.assertEqual(struct.unpack_from("<I", new, 8)[0], len(new))

    def test_bytecode_non_bmp_code_units_and_octet_tail_are_preserved(self):
        self.require_expanded_schema()
        rewrite = getattr(system_text, "rewrite_bytecode", None)
        self.assertIsNotNone(rewrite)
        source = (system_text.SOURCE_ROOT / "sysscn/advancedmenu.tjs").read_bytes()
        replacement = "中文与补充平面字符 \U0001f9ed(&A)"
        rebuilt = rewrite(source, {37: replacement})
        self.assertEqual(bytecode_strings(rebuilt)[37], replacement)

    def test_disabled_constant_edits_are_rejected(self):
        self.require_expanded_schema()
        document = copy.deepcopy(self.document)
        document["bytecode"]["sysscn/nocfchkdialog.tjs"]["strings"][21]["translation"] = "修改但未启用"
        with self.assertRaisesRegex(ValueError, "enable"):
            system_text.rebuild(document)

    def test_constant_index_and_candidate_types_are_exact_guards(self):
        self.require_expanded_schema()
        for field, replacement in (("index", True), ("candidate", 0)):
            document = copy.deepcopy(self.document)
            document["bytecode"]["sysscn/advancedmenu.tjs"]["strings"][1][field] = replacement
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    system_text.rebuild(document)

    def test_help_ks_document_edits_preserve_tags_and_original_wrapper(self):
        self.require_expanded_schema()
        document = copy.deepcopy(self.document)
        name = "sysscn/brows_conf.ks"
        row = document["documents"][name]
        self.assertIn("画面の表示方式を変更します。", row["source"])
        row.update(enabled=True, translation=row["source"].replace("画面の表示方式を変更します。", "可在这里更改画面的显示方式。"))
        outputs, changed = system_text.rebuild(document)
        decoded, wrapper, encoding = decode_text(outputs[name])
        self.assertEqual(changed, 1)
        self.assertIn("可在这里更改画面的显示方式。", decoded)
        self.assertEqual((wrapper, encoding), ("krkr_type1", "utf-16-le-bom"))
        row["translation"] = row["translation"].replace("[_fontsec]", "[_changed]")
        with self.assertRaises(ValueError):
            system_text.rebuild(document)

    def test_csv_and_ini_default_to_preserved_documents(self):
        self.require_expanded_schema()
        for name in ("main/soundlist.csv", "uipsd/dialog.ini"):
            row = self.document["documents"][name]
            self.assertFalse(row["enabled"])
            self.assertEqual(row["source"], row["translation"])

    def test_csv_title_edit_keeps_row_keys(self):
        self.require_expanded_schema()
        document = copy.deepcopy(self.document)
        row = document["documents"]["main/soundlist.csv"]
        row.update(enabled=True, translation=row["source"].replace("bgm001,いつか見た夢", "bgm001,曾经梦见的梦"))
        outputs, changed = system_text.rebuild(document)
        self.assertEqual(changed, 1)
        self.assertIn("bgm001,曾经梦见的梦", decode_text(outputs["main/soundlist.csv"])[0])
        row["translation"] = row["translation"].replace("bgm001,", "bgm999,")
        with self.assertRaises(ValueError):
            system_text.rebuild(document)

    def test_record_and_document_edits_cannot_overlap(self):
        self.require_expanded_schema()
        document = copy.deepcopy(self.document)
        row = next(item for item in document["records"] if item["id"] == "default:FastSkipModeText")
        row["translation"] = "正在快速跳过"
        whole = document["documents"]["main/default.tjs"]
        whole.update(enabled=True, translation=whole["source"].replace("高速スキップ中...", "冲突改动"))
        with self.assertRaisesRegex(ValueError, "overlap"):
            system_text.rebuild(document)


if __name__ == "__main__":
    unittest.main()
