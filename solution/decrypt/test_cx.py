from __future__ import annotations

import importlib
import unittest
from pathlib import Path


TITLE_ROOT = Path(__file__).resolve().parents[2]


class CxTests(unittest.TestCase):
    def load(self):
        self.assertTrue((Path(__file__).parent / "cx.py").is_file(), "Missing DC4 Cx implementation")
        return importlib.import_module("solution.decrypt.cx")

    def test_original_png_psb_and_opus_headers(self):
        cx = self.load()
        scheme = cx.load_scheme()
        fixtures = [
            (
                0xB6595F14,
                "d9001e175d5a4a5a5050505d19181402505050d0505050d858565050507f3db3f65050758c191411042851bcc557c80585a5a687ed83c96e539c503215e1f1f2",
                "89504e470d0a1a0a0000000d494844520000008000000088",
            ),
            (
                0x3A829397,
                "747766242724242408242424082424242fda26248326272444502724475027244250272411232424fe20cd692a89252a25242424252426242724202421242224",
                "50534200030000002c0000002c0000000bfe0200a7020300",
            ),
            (
                0x7D45A4B0,
                "7a52526635373535353535353535455535353535353578b2f30c34267a4540467d50545134373535b58e35353535357a52526635353535353535353535455535",
                "4f6767530002000000000000000070600000000000004d87",
            ),
        ]
        for file_hash, encrypted_hex, expected_hex in fixtures:
            with self.subTest(file_hash=hex(file_hash)):
                encrypted = bytes.fromhex(encrypted_hex)
                plaintext = scheme.decrypt(encrypted, file_hash)
                self.assertTrue(plaintext.startswith(bytes.fromhex(expected_hex)))
                self.assertEqual(scheme.decrypt(plaintext, file_hash), encrypted)

    def test_slices_across_split_and_byte_fix_positions(self):
        scheme = self.load().load_scheme()
        encrypted = bytes(range(256)) * 300
        file_hash = 0x3A829397
        plaintext = scheme.decrypt(encrypted, file_hash)
        cuts = [0, 1, 41, 42, 255, 256, 511, 1024, 4096, 32768, 65535, 65536, len(encrypted)]
        sliced = b"".join(scheme.decrypt(encrypted[a:b], file_hash, a) for a, b in zip(cuts, cuts[1:]))
        self.assertEqual(sliced, plaintext)
        self.assertEqual(scheme.decrypt(plaintext, file_hash), encrypted)

    def test_rejects_invalid_hash_and_offset(self):
        scheme = self.load().load_scheme()
        for file_hash, offset in [(-1, 0), (1 << 32, 0), (0, -1)]:
            with self.subTest(file_hash=file_hash, offset=offset):
                with self.assertRaises(ValueError):
                    scheme.decrypt(b"test", file_hash, offset)

    def test_native_equal_byte_fix_offsets_do_not_wrap_at_65535(self):
        cx = self.load()

        class NativeEdgeCase(cx.CxEncryption):
            def execute_xcode(self, file_hash):
                return 0x00776655, 0xFFFFFFFF

        cipher = object.__new__(NativeEdgeCase)
        data = bytearray(2)
        cipher.decode(0, 65535, data, 0, 2)
        self.assertEqual(data, bytes.fromhex("3322"))

    def test_extracts_key_from_this_executable(self):
        self.load()
        key = importlib.import_module("solution.decrypt.key")
        config, table = key.extract_key((TITLE_ROOT / "game/DC4_FD.exe").read_bytes())
        self.assertEqual(config["mask"], 0x1D4)
        self.assertEqual(config["offset"], 0x2A)
        self.assertEqual(config["source"]["module_sha256"], "c28025c7452b63e22b2841c4150a9965357196d3b23ad8e3b58a9d607c6827f4")
        self.assertEqual(len(table), 4096)

    def test_rejects_unverified_executable(self):
        self.load()
        key = importlib.import_module("solution.decrypt.key")
        with self.assertRaises(ValueError):
            key.extract_key(b"MZ" + bytes(128))

    def test_verifies_original_representative_resources(self):
        self.assertTrue((Path(__file__).parent / "verify.py").is_file(), "Missing complete-payload sample verification")
        verify = importlib.import_module("solution.decrypt.verify")
        result = verify.verify_samples()
        self.assertEqual(result["sample_count"], 11)
        self.assertTrue(all(item["checksum_matches"] for item in result["samples"]))
        self.assertEqual(result["media_decoded"], {"png": 3, "opus": 2})


if __name__ == "__main__":
    unittest.main()
