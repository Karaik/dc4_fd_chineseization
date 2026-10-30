"""DC4 FD PSB/SCN UTF-8 text extraction and verified compilation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FORMAT = 'dc4-scn-text-v1'
MANIFEST = '.dc4_scn_manifest.json'
CONTROL = re.compile(r'\\n|%i\d+&[^;]+;')
IMAGE = re.compile(r'%i\d+&[^;]+;')
PAIR = re.compile(r'^([○●])([0-9a-f]{24})\1(.*)$')
_SOURCE = object()


class ScriptError(ValueError):
    pass


@dataclass
class Record:
    id: str
    kind: str
    source: str
    paths: list[tuple]
    expected: list[Any] = field(default_factory=list)
    body_paths: set[tuple] = field(default_factory=set)


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_sources(root: Path) -> list[Path]:
    paths = sorted(p for p in Path(root).rglob('*.json') if not p.name.endswith('.resx.json') and not p.name.startswith('.'))
    if not paths:
        raise ScriptError(f'No decompiled SCN JSON files: {root}')
    for path in paths:
        meta = path.with_suffix('.resx.json')
        if not meta.is_file() or load_json(meta).get('PsbType') != 'Scn':
            raise ScriptError(f'Missing or non-SCN FreeMote metadata: {meta}')
    return paths


def _tool(root: Path, name: str) -> Path:
    root = Path(root).resolve()
    path = root / name if root.is_dir() else root.parent / name
    if not path.is_file():
        raise ScriptError(f'Missing FreeMote tool: {path}')
    return path


def _run(args: list[str], cwd: Path, log: Path) -> None:
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=300)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ScriptError(f'FreeMote failed ({result.returncode}); see {log}')


def _batches(items: list, size: int = 30):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _readable(doc: dict, path: Path) -> None:
    lines = ['# DC4 FD SCN instructions; generated from the adjacent FreeMote JSON.']
    lines.append('HEADER ' + json.dumps({k: v for k, v in doc.items() if k != 'scenes'}, ensure_ascii=False))
    for si, scene in enumerate(doc['scenes']):
        lines.append(f'SCENE {si} ' + json.dumps({k: v for k, v in scene.items() if k not in ('lines', 'texts')}, ensure_ascii=False))
        for li, line in enumerate(scene['lines']):
            lines.append(f'LINE {si}:{li} ' + json.dumps(line, ensure_ascii=False, separators=(',', ':')))
        for ti, text in enumerate(scene.get('texts', [])):
            lines.append(f'TEXT {si}:{ti} ' + json.dumps(text, ensure_ascii=False, separators=(',', ':')))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def ensure_decompile(input_scn_dir: Path, output_json_dir: Path, tool: Path, *, force: bool = False) -> dict:
    source, output = Path(input_scn_dir).resolve(), Path(output_json_dir).resolve()
    decompiler = _tool(tool, 'PsbDecompile.exe')
    inputs = sorted(source.rglob('*.scn'))
    if not inputs:
        raise ScriptError(f'No SCN inputs: {source}')
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST
    previous = load_json(manifest_path) if manifest_path.is_file() else {}
    previous_files = previous.get('inputs', {}) if previous.get('tool_sha256') == _sha(decompiler) else {}
    entries = {}
    pending: dict[Path, list[Path]] = {}
    for path in inputs:
        rel = path.relative_to(source)
        if not path.read_bytes().startswith(b'PSB\0\x03\0\0\0'):
            raise ScriptError(f'Expected decrypted PSB v3 SCN: {path}')
        target = output / rel.with_suffix('.json')
        item = {'source_sha256': _sha(path), 'json': rel.with_suffix('.json').as_posix()}
        old = previous_files.get(rel.as_posix(), {})
        metadata = target.with_suffix('.resx.json')
        if force or old.get('source_sha256') != item['source_sha256'] or not target.is_file() or old.get('json_sha256') != _sha(target) or not metadata.is_file() or old.get('resx_sha256') != _sha(metadata):
            pending.setdefault(target.parent, []).append(path)
        entries[rel.as_posix()] = item
    expected_json = {output / item['json'] for item in entries.values()}
    existing_json = {p for p in output.rglob('*.json') if not p.name.startswith('.') and not p.name.endswith('.resx.json')}
    if existing_json - expected_json:
        raise ScriptError(f'Stale or unknown JSON files in cache: {sorted(map(str, existing_json - expected_json))}')
    for folder, paths in pending.items():
        folder.mkdir(parents=True, exist_ok=True)
        for index, batch in enumerate(_batches(paths)):
            _run([str(decompiler), '-indent', '-o', str(folder), *(str(p) for p in batch)], output, folder / f'.decompile_{index}.log')
    scenes = blocks = 0
    for rel, item in entries.items():
        path = output / item['json']
        doc = load_json(path)
        collect_records(doc, item['json'])
        meta = load_json(path.with_suffix('.resx.json'))
        if meta.get('PsbVersion') != 3 or meta.get('PsbType') != 'Scn' or meta.get('Resources'):
            raise ScriptError(f'Unsupported SCN metadata or binary resources: {path}')
        _readable(doc, path.with_suffix('.ins.txt'))
        item['json_sha256'] = _sha(path)
        item['resx_sha256'] = _sha(path.with_suffix('.resx.json'))
        scenes += len(doc['scenes'])
        blocks += sum(len(t[1]) for s in doc['scenes'] for t in s.get('texts', []))
    _write_json(manifest_path, {'format': FORMAT, 'encoding': 'utf-8', 'tool_sha256': _sha(decompiler), 'inputs': entries})
    return {'files': len(inputs), 'decompiled': sum(map(len, pending.values())), 'scenes': scenes, 'body_blocks': blocks, 'json_root': str(output)}


def _walk(value: Any, path: tuple = ()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + (index,))


def collect_records(doc: dict, script_name: str) -> list[Record]:
    if not isinstance(doc, dict) or set(doc) != {'hash', 'name', 'outlines', 'scenes'} or not isinstance(doc['scenes'], list):
        raise ScriptError(f'Unsupported DC4 SCN root: {script_name}')
    grouped: dict[tuple[str, str], Record] = {}

    def add(kind: str, value: Any, path: tuple, *, expected: Any = _SOURCE, body: bool = False):
        if value is None or value == '':
            return
        if not isinstance(value, str):
            raise ScriptError(f'Expected visible string: {script_name} {path}')
        key = kind, value
        if key not in grouped:
            stable = hashlib.sha256((script_name + '\0' + kind + '\0' + value).encode('utf-8')).hexdigest()[:24]
            grouped[key] = Record(stable, kind, value, [])
        if path not in grouped[key].paths:
            grouped[key].paths.append(path)
            grouped[key].expected.append(value if expected is _SOURCE else expected)
        if body:
            grouped[key].body_paths.add(path)

    for si, scene in enumerate(doc['scenes']):
        sp = ('scenes', si)
        if not isinstance(scene, dict) or not isinstance(scene.get('lines'), list):
            raise ScriptError(f'Unsupported scene: {script_name} {si}')
        add('title', scene.get('title'), sp + ('title',))
        for ti, text in enumerate(scene.get('texts', [])):
            tp = sp + ('texts', ti)
            if not isinstance(text, list) or len(text) not in (5, 6) or not isinstance(text[1], list):
                raise ScriptError(f'Unsupported text entry: {script_name} {tp}')
            for bi, block in enumerate(text[1]):
                bp = tp + (1, bi)
                if not isinstance(block, list) or len(block) not in (3, 4, 5) or not isinstance(block[1], str) or type(block[2]) is not int:
                    raise ScriptError(f'Unsupported text block: {script_name} {bp}')
                # Keep the logical speaker/layer IDs intact. The native block
                # override is already used by the game for hidden/group names.
                if block[0] is not None:
                    add('speaker', block[0], bp + (0,))
                elif isinstance(text[0], str) and not text[0].startswith('comu'):
                    add('speaker', text[0], bp + (0,), expected=None)
                if IMAGE.fullmatch(block[1]):
                    if len(block) != 4 or block[2] != 1:
                        raise ScriptError(f'Unsupported image block: {script_name} {bp}')
                    add('image_alt', block[3], bp + (3,))
                else:
                    speech, history, length = render_text(block[1])
                    if block[2:] != [length, speech, history][:len(block) - 2]:
                        raise ScriptError(f'Derived text cache mismatch: {script_name} {bp}')
                    kind = 'message' if isinstance(text[0], str) and text[0].startswith('comu') else 'body'
                    add(kind, block[1], bp + (1,), body=True)
        for li, line in enumerate(scene['lines']):
            if not isinstance(line, list) or not line or not isinstance(line[0], str):
                continue
            op = line[0]
            for i in range(1, len(line) - 1, 2):
                key, value = line[i:i + 2]
                path = sp + ('lines', li, i + 1)
                if op == 'autosave' and key == 'text':
                    add('autosave', value, path)
                elif op == 'comumode' and key == 'text':
                    add('message', value, path)
        for ci, choice in enumerate(scene.get('selects', [])):
            add('choice', choice.get('text'), sp + ('selects', ci, 'text'))
    # Phone messages are also copied into numeric line states and text states.
    for path, value in _walk(doc):
        if isinstance(value, dict) and 'comumode' in value:
            if not isinstance(value['comumode'], list):
                raise ScriptError(f'Unsupported phone state: {script_name} {path}')
            for i, message in enumerate(value['comumode']):
                if isinstance(message, dict):
                    add('message', message.get('text'), path + ('comumode', i, 'text'))
    return list(grouped.values())


def render_text(text: str) -> tuple[str, str, int]:
    """Return speech, history, visible UTF-16 length for the observed SCN markup."""
    if '\0' in text or '\r' in text or '\n' in text:
        raise ScriptError('Use the SCN literal \\n marker instead of physical line breaks or NUL')
    text.encode('utf-8', errors='strict')
    speech, history = [], []
    pos = 0
    while pos < len(text):
        if text.startswith('\\n', pos):
            pos += 2
        elif text[pos] == '[':
            end = text.find(']', pos + 1)
            if end < 0:
                raise ScriptError('Malformed ruby marker')
            ruby = text[pos + 1:end]
            reading, comma, count = ruby.rpartition(',')
            if not comma:
                reading, length = ruby, 1
            elif count.isdigit():
                length = int(count) + 1
            else:
                raise ScriptError('Malformed ruby length')
            base = text[end + 1:end + 1 + length]
            if not reading or len(base) != length or any(c in base for c in '[]\\'):
                raise ScriptError('Malformed ruby base')
            history.append(base)
            speech.append(base if reading == '・' else reading.replace('・', ''))
            pos = end + 1 + length
        elif text[pos] in ']\\':
            raise ScriptError('Unknown text control marker')
        elif text[pos] == '%':
            match = IMAGE.match(text, pos)
            if not match:
                raise ScriptError('Unknown inline resource marker')
            raise ScriptError('Inline images must use the observed standalone image block')
        else:
            speech.append(text[pos])
            history.append(text[pos])
            pos += 1
    plain = ''.join(history)
    return ''.join(speech), plain, len(plain.encode('utf-16-le')) // 2


def format_records(records: list[Record]) -> str:
    lines = [f'# {FORMAT}; JSON strings preserve escaping; keep IDs and source lines intact.']
    for record in records:
        lines.extend((f'# {record.kind}; occurrences={len(record.paths)}',
                      f'○{record.id}○{json.dumps(record.source, ensure_ascii=False)}',
                      f'●{record.id}●{json.dumps(record.source, ensure_ascii=False)}', ''))
    return '\n'.join(lines).rstrip() + '\n'


def parse_translation(text: str, records: list[Record]) -> dict[str, str]:
    expected = {record.id: record for record in records}
    originals, translations = {}, {}
    for no, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        match = PAIR.fullmatch(line)
        if not match:
            raise ScriptError(f'Malformed translation line: {no}')
        marker, rid, raw = match.groups()
        target = originals if marker == '○' else translations
        if rid in target:
            raise ScriptError(f'Duplicate ID: {rid}')
        if rid not in expected:
            raise ScriptError(f'Unknown ID: {rid}')
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScriptError(f'Invalid JSON string at line {no}') from exc
        if not isinstance(value, str):
            raise ScriptError(f'Expected JSON string at line {no}')
        if marker == '○' and value != expected[rid].source:
            raise ScriptError(f'Source mismatch: {rid}')
        target[rid] = value
    if set(originals) != set(expected) or set(translations) != set(expected):
        raise ScriptError(f'Missing IDs: source={len(expected) - len(originals)} translation={len(expected) - len(translations)}')
    return translations


def _get(root: Any, path: tuple) -> Any:
    for key in path:
        root = root[key]
    return root


def apply_translations(doc: dict, records: list[Record], translations: dict[str, str]) -> int:
    if set(translations) != {r.id for r in records}:
        raise ScriptError('Missing or unknown translation IDs')
    changed = 0
    for record in records:
        target = translations[record.id]
        if not isinstance(target, str) or '\0' in target or '\r' in target or '\n' in target:
            raise ScriptError(f'Invalid translation string: {record.id}')
        target.encode('utf-8', errors='strict')
        if Counter(CONTROL.findall(target)) != Counter(CONTROL.findall(record.source)):
            raise ScriptError(f'Control marker mismatch: {record.id}')
        for path, expected in zip(record.paths, record.expected):
            if _get(doc, path) != expected:
                raise ScriptError(f'Source mismatch in JSON: {record.id} {path}')
        if target == record.source:
            continue
        if not target:
            raise ScriptError(f'Empty translation is not allowed: {record.id}')
        changed += 1
        derived = render_text(target) if record.body_paths else None
        for path in record.paths:
            parent = _get(doc, path[:-1])
            parent[path[-1]] = target
            if derived and path in record.body_paths:
                speech, history, length = derived
                parent[2] = length
                if len(parent) >= 4:
                    parent[3] = speech
                if len(parent) >= 5:
                    parent[4] = history
    return changed


def export_text(jsonroot: Path, raw: Path, translated: Path) -> dict:
    root, raw, translated = Path(jsonroot), Path(raw), Path(translated)
    summary = {'files': 0, 'records': 0, 'occurrences': 0, 'created_translations': 0, 'by_kind': {}}
    counts = Counter()
    for path in json_sources(root):
        rel = path.relative_to(root)
        records = collect_records(load_json(path), rel.as_posix())
        text = format_records(records)
        output = rel.with_suffix('.txt')
        baseline, editable = raw / output, translated / output
        baseline.parent.mkdir(parents=True, exist_ok=True)
        editable.parent.mkdir(parents=True, exist_ok=True)
        if baseline.exists() and baseline.read_text('utf-8-sig') != text:
            raise ScriptError(f'Existing raw baseline differs: {baseline}')
        if not baseline.exists():
            baseline.write_text(text, encoding='utf-8')
        if editable.exists():
            parse_translation(editable.read_text('utf-8-sig'), records)
        else:
            editable.write_text(text, encoding='utf-8')
            summary['created_translations'] += 1
        summary['files'] += 1
        summary['records'] += len(records)
        summary['occurrences'] += sum(len(r.paths) for r in records)
        counts.update(r.kind for r in records)
    summary['by_kind'] = dict(sorted(counts.items()))
    return summary


def _difference(a: Any, b: Any, path: str = '') -> str | None:
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return path + ': dictionary keys differ'
        for key in a:
            found = _difference(a[key], b[key], path + '/' + str(key))
            if found:
                return found
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return path + ': list lengths differ'
        for i, (left, right) in enumerate(zip(a, b)):
            found = _difference(left, right, path + '/' + str(i))
            if found:
                return found
    elif a != b or type(a) not in (int, float) and type(a) != type(b):
        return path + f': {a!r} != {b!r}'
    return None


def compile_translations(jsonroot: Path, translated: Path, outbin: Path, tool: Path, verify: bool = True) -> dict:
    root, translated, outbin = Path(jsonroot).resolve(), Path(translated).resolve(), Path(outbin).resolve()
    compiler = _tool(tool, 'PsBuild.exe')
    sources = json_sources(root)
    wanted = {p.relative_to(root).with_suffix('.txt') for p in sources}
    actual = {p.relative_to(translated) for p in translated.rglob('*.txt')}
    if actual != wanted:
        raise ScriptError(f'Translation file mismatch; missing={sorted(map(str, wanted - actual))} extra={sorted(map(str, actual - wanted))}')
    # Validate the entire input before invoking a compiler or writing a binary.
    prepared = []
    changed = 0
    for path in sources:
        rel = path.relative_to(root)
        doc = load_json(path)
        records = collect_records(doc, rel.as_posix())
        translations = parse_translation((translated / rel.with_suffix('.txt')).read_text('utf-8-sig'), records)
        changed += apply_translations(doc, records, translations)
        prepared.append((rel, doc))
    if outbin.exists() and any(outbin.iterdir()):
        raise ScriptError(f'Compilation output must be empty: {outbin}')
    outbin.mkdir(parents=True, exist_ok=True)
    work = outbin.parent / ('.' + outbin.name + '_work_' + uuid.uuid4().hex[:12])
    stage, compiled, checked = work / 'json', work / 'compiled', work / 'verified'
    compiled.mkdir(parents=True)
    groups: dict[Path, list[tuple[Path, dict]]] = {}
    for rel, doc in prepared:
        path = stage / rel
        _write_json(path, doc)
        shutil.copyfile(root / rel.with_suffix('.resx.json'), path.with_suffix('.resx.json'))
        groups.setdefault(rel.parent, []).append((rel, doc))
    binaries = []
    for folder, items in groups.items():
        destination = compiled / folder
        destination.mkdir(parents=True, exist_ok=True)
        for index, batch in enumerate(_batches(items)):
            _run([str(compiler), *(str(stage / rel) for rel, _ in batch)], destination, work / f'compile_{folder.name}_{index}.log')
        for rel, doc in items:
            generated = destination / (rel.stem + '.pure.scn')
            target = outbin / rel.with_suffix('.scn')
            if not generated.is_file() or not generated.read_bytes().startswith(b'PSB\0\x03\0\0\0'):
                raise ScriptError(f'Compiler did not produce PSB v3: {generated}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(generated, target)
            binaries.append(target)
    verified = 0
    if verify:
        ensure_decompile(outbin, checked, tool)
        for rel, expected in prepared:
            actual_doc = load_json(checked / rel)
            difference = _difference(expected, actual_doc)
            if difference:
                raise ScriptError(f'Recompiled SCN differs: {rel} {difference}')
            verified += 1
    summary = {'files': len(sources), 'verified': verified, 'changed_records': changed, 'encoding': 'utf-8',
               'binary_files': [str(p) for p in binaries], 'verification_json': str(checked), 'work': str(work),
               'comparison': 'complete JSON tree; numeric int/float representations compare by value'}
    source_manifest = root / MANIFEST
    if source_manifest.is_file():
        hashes = load_json(source_manifest).get('inputs', {})
        summary['source_byte_identical'] = sum(
            _sha(path) == hashes.get(path.relative_to(outbin).as_posix(), {}).get('source_sha256')
            for path in binaries)
    _write_json(work / 'roundtrip_summary.json', summary)
    return summary
