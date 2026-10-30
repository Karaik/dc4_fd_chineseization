# Cx algorithm reference: GARbro KiriKiriCx.cs
# https://github.com/crskycode/GARbro/blob/ed4ff6de9ae80d6eab0c37a08671c91fa1855240/ArcFormats/KiriKiri/KiriKiriCx.cs
# Python reference: YuriSizuku/GalgameReverse, commit a189c9831091c3033429ca5890dbdfb134ef3dd8,
# project/krkr/src/krkr_hxcrypt.py (Cx subset).
# Copyright (C) 2014-2016 by morkt
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List


@lru_cache(maxsize=256)
def _xor_table(key: int) -> bytes:
    return bytes(value ^ key for value in range(256))


class CxProgramException(Exception):
    pass

class InvalidEncryptionScheme(Exception):
    pass

class CxByteCode:
    NOP = 0
    RETN = 1
    MOV_EDI_ARG = 2
    PUSH_EBX = 3
    POP_EBX = 4
    PUSH_ECX = 5
    POP_ECX = 6
    MOV_EAX_EBX = 7
    MOV_EBX_EAX = 8
    MOV_ECX_EBX = 9
    MOV_EAX_CONTROL_BLOCK = 10
    MOV_EAX_EDI = 11
    MOV_EAX_INDIRECT = 12
    ADD_EAX_EBX = 13
    SUB_EAX_EBX = 14
    IMUL_EAX_EBX = 15
    AND_ECX_0F = 16
    SHR_EBX_1 = 17
    SHL_EAX_1 = 18
    SHR_EAX_CL = 19
    SHL_EAX_CL = 20
    OR_EAX_EBX = 21
    NOT_EAX = 22
    NEG_EAX = 23
    DEC_EAX = 24
    INC_EAX = 25

    IMMED = 0x100
    MOV_EAX_IMMED = 0x101
    AND_EBX_IMMED = 0x102
    AND_EAX_IMMED = 0x103
    XOR_EAX_IMMED = 0x104
    ADD_EAX_IMMED = 0x105
    SUB_EAX_IMMED = 0x106

@dataclass
class CxScheme:
    mask = 0
    offset = 0
    prolog_order: List[int] = None
    odd_branch_order: List[int] = None
    even_branch_order: List[int] = None
    control_block: List[int] = None

class CxProgram:
    LENGTH_LIMIT = 0x80

    class Context:
        def __init__(self):
            self.eax = 0
            self.ebx = 0
            self.ecx = 0
            self.edi = 0
            self.stack = []

    def __init__(self, seed, control_block):
        self.code = []
        self.control_block = control_block
        self.length = 0
        self.seed = seed

    def execute(self, hash_val):
        ctx = self.Context()
        iterator = iter(self.code)

        try:
            while True:
                bytecode = next(iterator)
                immed = 0

                if (bytecode & CxByteCode.IMMED) == CxByteCode.IMMED:
                    immed = next(iterator)

                bc_type = bytecode

                if bc_type == CxByteCode.NOP: pass
                elif bc_type == CxByteCode.IMMED: pass
                elif bc_type == CxByteCode.MOV_EDI_ARG: ctx.edi = hash_val
                elif bc_type == CxByteCode.PUSH_EBX: ctx.stack.append(ctx.ebx)
                elif bc_type == CxByteCode.POP_EBX: ctx.ebx = ctx.stack.pop()
                elif bc_type == CxByteCode.PUSH_ECX: ctx.stack.append(ctx.ecx)
                elif bc_type == CxByteCode.POP_ECX: ctx.ecx = ctx.stack.pop()
                elif bc_type == CxByteCode.MOV_EBX_EAX: ctx.ebx = ctx.eax
                elif bc_type == CxByteCode.MOV_EAX_EDI: ctx.eax = ctx.edi
                elif bc_type == CxByteCode.MOV_ECX_EBX: ctx.ecx = ctx.ebx
                elif bc_type == CxByteCode.MOV_EAX_EBX: ctx.eax = ctx.ebx

                elif bc_type == CxByteCode.AND_ECX_0F: ctx.ecx &= 0x0f
                elif bc_type == CxByteCode.SHR_EBX_1: ctx.ebx >>= 1
                elif bc_type == CxByteCode.SHL_EAX_1: ctx.eax = (ctx.eax << 1) & 0xFFFFFFFF
                elif bc_type == CxByteCode.SHR_EAX_CL: ctx.eax >>= (ctx.ecx & 0x1F)
                elif bc_type == CxByteCode.SHL_EAX_CL: ctx.eax = (ctx.eax << (ctx.ecx & 0x1F)) & 0xFFFFFFFF
                elif bc_type == CxByteCode.OR_EAX_EBX: ctx.eax |= ctx.ebx
                elif bc_type == CxByteCode.NOT_EAX: ctx.eax = (~ctx.eax) & 0xFFFFFFFF
                elif bc_type == CxByteCode.NEG_EAX: ctx.eax = (-ctx.eax) & 0xFFFFFFFF
                elif bc_type == CxByteCode.DEC_EAX: ctx.eax = (ctx.eax - 1) & 0xFFFFFFFF
                elif bc_type == CxByteCode.INC_EAX: ctx.eax = (ctx.eax + 1) & 0xFFFFFFFF

                elif bc_type == CxByteCode.ADD_EAX_EBX: ctx.eax = (ctx.eax + ctx.ebx) & 0xFFFFFFFF
                elif bc_type == CxByteCode.SUB_EAX_EBX: ctx.eax = (ctx.eax - ctx.ebx) & 0xFFFFFFFF
                elif bc_type == CxByteCode.IMUL_EAX_EBX: ctx.eax = (ctx.eax * ctx.ebx) & 0xFFFFFFFF

                elif bc_type == CxByteCode.ADD_EAX_IMMED: ctx.eax = (ctx.eax + immed) & 0xFFFFFFFF
                elif bc_type == CxByteCode.SUB_EAX_IMMED: ctx.eax = (ctx.eax - immed) & 0xFFFFFFFF
                elif bc_type == CxByteCode.AND_EBX_IMMED: ctx.ebx &= immed
                elif bc_type == CxByteCode.AND_EAX_IMMED: ctx.eax &= immed
                elif bc_type == CxByteCode.XOR_EAX_IMMED: ctx.eax ^= immed
                elif bc_type == CxByteCode.MOV_EAX_IMMED: ctx.eax = immed
                elif bc_type == CxByteCode.MOV_EAX_INDIRECT:
                    if ctx.eax >= len(self.control_block):
                        raise CxProgramException("Index out of bounds in CxEncryption program")
                    ctx.eax = self.control_block[ctx.eax]

                elif bc_type == CxByteCode.RETN:
                    if len(ctx.stack) > 0:
                        raise CxProgramException("Imbalanced stack in CxEncryption program")
                    return ctx.eax

                else:
                    raise CxProgramException(f"Invalid bytecode {bc_type} in CxEncryption program")

        except StopIteration:
            raise CxProgramException("CxEncryption program without RETN bytecode")

    def clear(self):
        self.length = 0
        self.code.clear()

    def emit_nop(self, count):
        if self.length + count > self.LENGTH_LIMIT:
            return False
        self.length += count
        return True

    def emit(self, code, length=1):
        if self.length + length > self.LENGTH_LIMIT:
            return False
        self.length += length
        self.code.append(code)
        return True

    def emit_uint32(self, x):
        if self.length + 4 > self.LENGTH_LIMIT:
            return False
        self.length += 4
        self.code.append(x)
        return True

    def emit_random(self):
        return self.emit_uint32(self.get_random())

    def get_random(self):
        seed = self.seed
        self.seed = (1103515245 * seed + 12345) & 0xFFFFFFFF
        res = self.seed ^ ((seed << 16) & 0xFFFFFFFF) ^ (seed >> 16)
        return res

class CxEncryption:
    S_CTL_BLOCK_SIGNATURE = b" Encryption control block"

    def __init__(self, scheme):
        self.m_mask = scheme.mask
        self.m_offset = scheme.offset

        self.prolog_order = scheme.prolog_order
        self.odd_branch_order = scheme.odd_branch_order
        self.even_branch_order = scheme.even_branch_order

        self.control_block = scheme.control_block
        self.m_program_list: List[CxProgram] = [None] * 0x80

    def __str__(self):
        return f"CxEncryption(0x{self.m_mask:X}, 0x{self.m_offset:X})"

    def get_base_offset(self, hash_val):
        return ((hash_val & self.m_mask) + self.m_offset) & 0xFFFFFFFF

    def decrypt_byte(self, hash_val, offset, value):
        key = hash_val
        base_offset = self.get_base_offset(key)

        if offset >= base_offset:
            key = ((key >> 16) ^ key) & 0xFFFFFFFF

        buffer = bytearray([value])
        self.decode(key, offset, buffer, 0, 1)
        return buffer[0]

    def decrypt_buffer(self, hash_val, offset, buffer, pos, count):
        key = hash_val
        base_offset = self.get_base_offset(key)

        if offset < base_offset:
            base_length = min(base_offset - offset, count)
            base_length = int(base_length)

            self.decode(key, offset, buffer, pos, base_length)
            offset += base_length
            pos += base_length
            count -= base_length

        if count > 0:
            key = ((key >> 16) ^ key) & 0xFFFFFFFF
            self.decode(key, offset, buffer, pos, count)

    def decode(self, key, offset, buffer, pos, count):
        ret1, ret2 = self.execute_xcode(key)

        key1 = ret2 >> 16
        key2 = ret2 & 0xFFFF
        key3 = ret1 & 0xFF

        if key1 == key2:
            key2 += 1
        if key3 == 0:
            key3 = 1

        if offset <= key2 < offset + count:
            idx = pos + int(key2 - offset)
            buffer[idx] ^= (ret1 >> 16) & 0xFF

        if offset <= key1 < offset + count:
            idx = pos + int(key1 - offset)
            buffer[idx] ^= (ret1 >> 8) & 0xFF

        buffer[pos : pos + count] = buffer[pos : pos + count].translate(_xor_table(key3))

    def execute_xcode(self, hash_val):
        seed = hash_val & 0x7f
        if self.m_program_list[seed] is None:
            self.m_program_list[seed] = self.generate_program(seed)

        program = self.m_program_list[seed]

        hash_shifted = hash_val >> 7
        ret1 = program.execute(hash_shifted)
        ret2 = program.execute((~hash_shifted) & 0xFFFFFFFF)

        return (ret1, ret2)

    def generate_program(self, seed):
        program = self.new_program(seed)
        for stage in range(5, 0, -1):
            if self.emit_code(program, stage):
                return program
            program.clear()

        raise CxProgramException("Overly large CxEncryption bytecode")

    def new_program(self, seed):
        return CxProgram(seed, self.control_block)

    def emit_code(self, program, stage):
        return (program.emit_nop(5)
                and program.emit(CxByteCode.MOV_EDI_ARG, 4)
                and self.emit_body(program, stage)
                and program.emit_nop(5)
                and program.emit(CxByteCode.RETN))

    def emit_body(self, program, stage):
        if stage == 1:
            return self.emit_prolog(program)

        if not program.emit(CxByteCode.PUSH_EBX):
            return False

        if (program.get_random() & 1) != 0:
            if not self.emit_body(program, stage - 1):
                return False
        elif not self.emit_body2(program, stage - 1):
            return False

        if not program.emit(CxByteCode.MOV_EBX_EAX, 2):
            return False

        if (program.get_random() & 1) != 0:
            if not self.emit_body(program, stage - 1):
                return False
        elif not self.emit_body2(program, stage - 1):
            return False

        return self.emit_odd_branch(program) and program.emit(CxByteCode.POP_EBX)

    def emit_body2(self, program, stage):
        if stage == 1:
            return self.emit_prolog(program)

        rc = True
        if (program.get_random() & 1) != 0:
            rc = self.emit_body(program, stage - 1)
        else:
            rc = self.emit_body2(program, stage - 1)

        return rc and self.emit_even_branch(program)

    def emit_prolog(self, program):
        rc = True
        choice = self.prolog_order[program.get_random() % 3]

        if choice == 2:
            rc = (program.emit_nop(5)
                  and program.emit(CxByteCode.MOV_EAX_IMMED, 2)
                  and program.emit_uint32(program.get_random() & 0x3ff)
                  and program.emit(CxByteCode.MOV_EAX_INDIRECT, 0))
        elif choice == 1:
             rc = program.emit(CxByteCode.MOV_EAX_EDI, 2)
        elif choice == 0:
             rc = (program.emit(CxByteCode.MOV_EAX_IMMED)
                   and program.emit_random())
        return rc

    def emit_even_branch(self, program):
        rc = True
        choice = self.even_branch_order[program.get_random() & 7]

        if choice == 0:
            rc = program.emit(CxByteCode.NOT_EAX, 2)
        elif choice == 1:
            rc = program.emit(CxByteCode.DEC_EAX)
        elif choice == 2:
            rc = program.emit(CxByteCode.NEG_EAX, 2)
        elif choice == 3:
            rc = program.emit(CxByteCode.INC_EAX)
        elif choice == 4:
            rc = (program.emit_nop(5)
                  and program.emit(CxByteCode.AND_EAX_IMMED)
                  and program.emit_uint32(0x3ff)
                  and program.emit(CxByteCode.MOV_EAX_INDIRECT, 3))
        elif choice == 5:
            rc = (program.emit(CxByteCode.PUSH_EBX)
                  and program.emit(CxByteCode.MOV_EBX_EAX, 2)
                  and program.emit(CxByteCode.AND_EBX_IMMED, 2)
                  and program.emit_uint32(0xaaaaaaaa)
                  and program.emit(CxByteCode.AND_EAX_IMMED)
                  and program.emit_uint32(0x55555555)
                  and program.emit(CxByteCode.SHR_EBX_1, 2)
                  and program.emit(CxByteCode.SHL_EAX_1, 2)
                  and program.emit(CxByteCode.OR_EAX_EBX, 2)
                  and program.emit(CxByteCode.POP_EBX))
        elif choice == 6:
            rc = (program.emit(CxByteCode.XOR_EAX_IMMED)
                  and program.emit_random())
        elif choice == 7:
            if (program.get_random() & 1) != 0:
                rc = program.emit(CxByteCode.ADD_EAX_IMMED)
            else:
                rc = program.emit(CxByteCode.SUB_EAX_IMMED)
            rc = rc and program.emit_random()

        return rc

    def emit_odd_branch(self, program):
        rc = True
        choice = self.odd_branch_order[program.get_random() % 6]

        if choice == 0:
            rc = (program.emit(CxByteCode.PUSH_ECX)
                  and program.emit(CxByteCode.MOV_ECX_EBX, 2)
                  and program.emit(CxByteCode.AND_ECX_0F, 3)
                  and program.emit(CxByteCode.SHR_EAX_CL, 2)
                  and program.emit(CxByteCode.POP_ECX))
        elif choice == 1:
            rc = (program.emit(CxByteCode.PUSH_ECX)
                  and program.emit(CxByteCode.MOV_ECX_EBX, 2)
                  and program.emit(CxByteCode.AND_ECX_0F, 3)
                  and program.emit(CxByteCode.SHL_EAX_CL, 2)
                  and program.emit(CxByteCode.POP_ECX))
        elif choice == 2:
            rc = program.emit(CxByteCode.ADD_EAX_EBX, 2)
        elif choice == 3:
            rc = (program.emit(CxByteCode.NEG_EAX, 2)
                  and program.emit(CxByteCode.ADD_EAX_EBX, 2))
        elif choice == 4:
            rc = program.emit(CxByteCode.IMUL_EAX_EBX, 3)
        elif choice == 5:
            rc = program.emit(CxByteCode.SUB_EAX_EBX, 2)

        return rc


class Scheme:
    def __init__(self, config: dict, control: bytes):
        if config.get("schema_version") != 1 or config.get("algorithm") != "cxdec-v1":
            raise ValueError("Unsupported DC4 encryption scheme")
        if config.get("random_generator") != "lcg":
            raise ValueError("Unsupported Cx random generator")
        if len(control) != 4096:
            raise ValueError("Cx control block must contain 4096 bytes")
        if hashlib.sha256(control).hexdigest() != config.get("control_block_sha256"):
            raise ValueError("Cx control block digest mismatch")
        cipher_config = CxScheme()
        for field in ("mask", "offset"):
            value = config.get(field)
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"Invalid Cx {field}")
            setattr(cipher_config, field, value)
            setattr(self, field, value)
        for field, size in (("prolog_order", 3), ("odd_branch_order", 6), ("even_branch_order", 8)):
            values = config.get(field)
            if not isinstance(values, list) or any(type(value) is not int for value in values) or sorted(values) != list(range(size)):
                raise ValueError(f"Invalid Cx {field}")
            setattr(cipher_config, field, list(values))
        cipher_config.control_block = list(struct.unpack("<1024I", control))
        self.startup_tjs_not_encrypted = False
        self._cipher = CxEncryption(cipher_config)

    def decrypt(self, data: bytes, file_hash: int, offset: int = 0) -> bytes:
        if type(file_hash) is not int or not 0 <= file_hash <= 0xFFFFFFFF:
            raise ValueError("file_hash must be an unsigned 32-bit integer")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        buffer = bytearray(data)
        self._cipher.decrypt_buffer(file_hash, offset, buffer, 0, len(buffer))
        return bytes(buffer)

    encrypt = decrypt


def load_scheme(path: Path | None = None) -> Scheme:
    config_path = Path(path) if path is not None else Path(__file__).with_name("key.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    control_name = config.get("control_block")
    if not isinstance(control_name, str) or Path(control_name).name != control_name:
        raise ValueError("Control block must be a file next to key.json")
    return Scheme(config, config_path.with_name(control_name).read_bytes())
