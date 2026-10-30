from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from solution.unpack.xp3 import Entry, _check_output_path, extract_archive, read_index


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'solution/unpack/work'


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def key_fingerprint() -> str:
    result = hashlib.sha256()
    for name in ('cx.py', 'key.json', 'control_block.bin'):
        path = ROOT / 'solution/decrypt' / name
        result.update(name.encode('ascii'))
        result.update(path.read_bytes())
    return result.hexdigest()


def is_notice(entry: Entry) -> bool:
    mismatch = entry.size != sum(s.size for s in entry.segments) or entry.packed_size != sum(s.packed_size for s in entry.segments)
    return not entry.protected and mismatch and entry.name.startswith('$$$ This is a protected archive. $$$ ') and entry.name.endswith('.txt')


def _output(path: Path) -> Path:
    path = path.absolute()
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or resolved == ROOT:
        raise ValueError('Unpack output must be strictly inside the project')
    for protected in ('game', 'game_script', 'solution/patch', 'solution/decrypt', 'solution/runtime/src'):
        if resolved == ROOT / protected or resolved.is_relative_to(ROOT / protected):
            raise ValueError(f'Unpack output is a maintained input path: {path}')
    _check_output_path(ROOT, path)
    return path


def unpack_archive(archive: Path, output_root: Path = DEFAULT_OUTPUT, *, overwrite: bool = False, verify_cache: bool = False, index_only: bool = False) -> dict:
    archive = archive.resolve()
    output_root = _output(output_root)
    output = output_root / archive.stem
    _check_output_path(output_root, output)
    entries = read_index(archive)
    index = [{'protected':entry.protected, **asdict(entry)} for entry in entries]
    archive_hash = sha256(archive)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / f'{archive.stem}_index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if index_only:
        return {'archive':archive.name, 'entries':len(entries), 'index_only':True}
    usable = [entry for entry in entries if not is_notice(entry)]
    skipped = [entry.name for entry in entries if is_notice(entry)]
    fingerprint = key_fingerprint() if any(entry.protected for entry in usable) else None
    manifest_path = output_root / f'{archive.stem}_manifest.json'
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text(encoding='utf-8'))
        matches = old.get('source_sha256') == archive_hash and old.get('scheme_sha256') == fingerprint and len(old.get('files', [])) == len(usable)
        if matches:
            for item in old['files']:
                path = output / item['name']
                _check_output_path(output_root, path)
                if not path.is_file() or path.stat().st_size != item['bytes'] or path.stat().st_mtime_ns != item['mtime_ns']:
                    matches = False
                    break
                if verify_cache and sha256(path) != item['sha256']:
                    matches = False
                    break
        if matches:
            return {**old, 'cache_reused':True}
    scheme = None
    if any(entry.protected for entry in usable):
        from solution.decrypt.cx import load_scheme
        scheme = load_scheme()
    print(f'[unpack] {archive.name}: {len(usable)} resources; {len(skipped)} malformed protection notices excluded', flush=True)
    files = extract_archive(archive, output, scheme, select=lambda entry: not is_notice(entry), overwrite=overwrite, verify_checksum=True)
    for item in files:
        item['mtime_ns'] = (output / item['name']).stat().st_mtime_ns
    result = {'archive':archive.name, 'source_sha256':archive_hash, 'scheme_sha256':fingerprint, 'entries':len(entries), 'extracted':len(files), 'skipped_protection_notices':skipped, 'plaintext_checksums_verified':True, 'files':files}
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[done] {archive.name}: {len(files)} checksums verified', flush=True)
    return {**result, 'cache_reused':False}


def main() -> int:
    parser = argparse.ArgumentParser(description='Read and decrypt DC4 FD XP3 archives with complete plaintext checksum verification.')
    parser.add_argument('input', nargs='?', type=Path, default=ROOT / 'game')
    parser.add_argument('output', nargs='?', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--verify-cache', action='store_true')
    parser.add_argument('--index-only', action='store_true')
    parser.add_argument('--decompile', action='store_true')
    parser.add_argument('--psb-tools', type=Path, default=ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0')
    args = parser.parse_args()
    archives = sorted(args.input.glob('*.xp3')) if args.input.is_dir() else [args.input]
    if not archives or any(not path.is_file() for path in archives):
        raise FileNotFoundError('No input XP3 archives were found')
    summaries = []
    for archive in archives:
        result = unpack_archive(archive, args.output, overwrite=args.overwrite, verify_cache=args.verify_cache, index_only=args.index_only)
        if args.decompile and not args.index_only:
            scn_dir = args.output / archive.stem / 'scn'
            if scn_dir.is_dir():
                from solution.script.scn import ensure_decompile
                result['decompile'] = ensure_decompile(scn_dir, args.output / archive.stem / 'scn_json', args.psb_tools / 'PsbDecompile.exe')
        summaries.append({key:value for key, value in result.items() if key != 'files'})
    summary = _output(args.output) / 'extract_summary.json'
    summary.write_text(json.dumps({'archives':summaries}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
