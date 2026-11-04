"""Bounded PSB v3 MDF decoding and original-shell reconstruction."""
from __future__ import annotations

import struct
import zlib


MAX_PSB_SIZE = 256 * 1024 * 1024
PSB3 = b'PSB\0\x03\0\0\0'


def unwrap_psb(data: bytes) -> tuple[bytes, dict]:
    shell = {'type': 'none'}
    if data.startswith(b'mdf\0'):
        if len(data) < 10:
            raise ValueError('Truncated MDF header')
        size = struct.unpack_from('<I', data, 4)[0]
        if size > MAX_PSB_SIZE:
            raise ValueError('MDF declared length exceeds the PSB size limit')
        cmf, flags = data[8:10]
        if cmf != 0x78 or flags & 0x20 or (cmf * 256 + flags) % 31:
            raise ValueError('Unsupported MDF zlib stream header')
        try:
            decoder = zlib.decompressobj()
            plain = decoder.decompress(data[8:], size + 1)
        except zlib.error as error:
            raise ValueError('Invalid MDF zlib stream') from error
        if len(plain) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('MDF length, checksum or compressed boundary mismatch')
        shell = {'type': 'mdf', 'compression_level': (1, 3, 6, 9)[flags >> 6]}
        data = plain
    if len(data) > MAX_PSB_SIZE or not data.startswith(PSB3):
        raise ValueError('Expected a decrypted PSB v3 payload within the size limit')
    return data, shell


def wrap_psb(data: bytes, shell: dict) -> bytes:
    plain, original = unwrap_psb(data)
    if original['type'] != 'none':
        raise ValueError('Shell reconstruction requires an unwrapped PSB')
    if shell == {'type': 'none'}:
        return plain
    if not isinstance(shell, dict) or set(shell) != {'type', 'compression_level'} or shell['type'] != 'mdf':
        raise ValueError('Unsupported original PSB shell metadata')
    level = shell['compression_level']
    if type(level) is not int or level not in (1, 3, 6, 9):
        raise ValueError('Unsupported original MDF compression level')
    result = b'mdf\0' + struct.pack('<I', len(plain)) + zlib.compress(plain, level)
    decoded, reconstructed = unwrap_psb(result)
    if decoded != plain or reconstructed != shell:
        raise ValueError('Reconstructed MDF did not verify')
    return result
