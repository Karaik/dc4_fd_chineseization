"""Import partial artist/translator returns into an isolated build candidate."""
from __future__ import annotations

from collections import defaultdict
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import stat
import uuid
import zipfile

from PIL import Image

from solution.unpack.xp3 import safe_name

ROOT = Path(__file__).resolve().parents[2]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _name(value: str) -> str:
    name = safe_name(value)
    if name != value or any(p.startswith('.') for p in name.split('/')):
        raise ValueError(f'Invalid or hidden return path: {value}')
    return name


def _plain(path: Path, boundary: Path) -> None:
    path, boundary = path.absolute(), boundary.absolute()
    if '..' in path.parts or '..' in boundary.parts or not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError(f'Path escapes its input directory: {path}')
    for part in (path, *path.parents):
        attrs = part.lstat()
        if part.is_symlink() or getattr(attrs, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'Symlink or reparse point is not supported: {part}')
        if part == boundary:
            break


def _archive(path: Path):
    try:
        import pyzipper
        return pyzipper.AESZipFile(path)
    except ImportError:
        return zipfile.ZipFile(path)


def _return_entries(source: Path, catalog: dict, password: bytes | None = None):
    source = Path(source).absolute()
    _plain(source, source.parent)
    names = catalog['files']
    aliases = {name.casefold(): name for name in names}
    if len(aliases) != len(names):
        raise ValueError('Case-colliding paths in the local handoff catalog')
    seen = set()

    def accept(name):
        name = _name(name)
        if name.casefold() in seen:
            raise ValueError(f'Duplicate return file: {name}')
        seen.add(name.casefold())
        if name.casefold() not in aliases:
            raise ValueError(f'Unknown returned file: {name}')
        return aliases[name.casefold()]

    if source.is_dir():
        prepared = []
        for path in sorted(source.rglob('*')):
            _plain(path, source)
            rel = path.relative_to(source).as_posix()
            _name(rel)
            if path.is_file():
                prepared.append((accept(rel), path))
            elif not path.is_dir():
                raise ValueError(f'Returned path is not a regular file or directory: {path}')
        for name, path in prepared:
            yield name, path.read_bytes()
    elif source.is_file():
        with _archive(source) as archive:
            prepared = []
            for item in archive.infolist():
                rel = item.filename.rstrip('/') if item.is_dir() else item.filename
                _name(rel)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or item.is_dir())):
                    raise ValueError(f'Non-regular ZIP entry: {item.filename}')
                if item.is_dir():
                    continue
                if getattr(item, 'orig_filename', item.filename) != item.filename:
                    raise ValueError('NUL in ZIP entry name')
                prepared.append((accept(item.filename), item))
            for name, item in prepared:
                try:
                    yield name, archive.read(item, pwd=password)
                except NotImplementedError as error:
                    raise ValueError('AES ZIP input requires pyzipper; install project requirements') from error
    else:
        raise ValueError(f'Missing return directory or ZIP: {source}')


def collect_return_files(source: Path, catalog: dict, password: bytes | None = None) -> dict[str, bytes]:
    """Convenience helper for small returns; production import streams members."""
    return dict(_return_entries(source, catalog, password))


def png_info(payload: bytes) -> dict:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG':
                raise ValueError('Returned images must be PNG files')
            rgba = image.convert('RGBA')
            return {'width': rgba.width, 'height': rgba.height, 'rgba_sha256': sha(rgba.tobytes())}
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f'Invalid returned PNG: {error}') from error


def image_changed(payload: bytes, row: dict, name: str) -> bool:
    actual = png_info(payload)
    if (actual['width'], actual['height']) != (row['width'], row['height']):
        raise ValueError(f'Image dimensions changed: {name}')
    return actual['rgba_sha256'] != row['rgba_sha256']


def review_system_edits(document: dict, reviewed: bool = False) -> tuple[dict, list[str]]:
    result = copy.deepcopy(document)
    pending = []
    for path, row in result.get('documents', {}).items():
        if row['translation'] != row['source'] and not row['enabled']:
            pending.append('document:' + path)
            row['enabled'] = bool(reviewed)
    for path, block in result.get('bytecode', {}).items():
        for row in block['strings']:
            if row['translation'] != row['source'] and not row['enabled']:
                pending.append(f'bytecode:{path}:{row["index"]}')
                row['enabled'] = bool(reviewed)
    if pending and not reviewed:
        raise ValueError('Program review required for disabled system edits; rerun with --reviewed-system-edits after review: ' + ', '.join(pending))
    return result, pending


def add_static(root: Path, name: str, data: bytes, records: dict, *, replace: bool = False) -> None:
    name = _name(name)
    folded = {key.casefold(): key for key in records}
    if name.casefold() in folded and folded[name.casefold()] != name:
        raise ValueError(f'Static path case conflict: {name}')
    path = root / name
    if path.exists() and path.read_bytes() != data and not replace:
        raise ValueError(f'Static resource conflict; review before explicit replacement: {name}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    records[name] = {'bytes': len(data), 'sha256': sha(data)}


def _scn_guard(payload: bytes, baseline: Path) -> None:
    pattern = re.compile(r'^([○●])([0-9a-f]{24})\1(.*)$')

    def read(text):
        source, translated = {}, {}
        for line in text.splitlines():
            if not line or line.startswith('#'):
                continue
            match = pattern.fullmatch(line)
            if not match:
                raise ValueError('Malformed SCN text line')
            mark, identity, encoded = match.groups()
            table = source if mark == '○' else translated
            if identity in table:
                raise ValueError('Duplicate SCN text ID')
            value = json.loads(encoded)
            if not isinstance(value, str):
                raise ValueError('SCN text must contain JSON strings')
            table[identity] = value
        if set(source) != set(translated):
            raise ValueError('SCN original and translation IDs differ')
        return source, translated

    original, _ = read(baseline.read_text('utf-8-sig'))
    returned, _ = read(payload.decode('utf-8-sig'))
    if returned != original:
        raise ValueError('SCN original text or IDs were changed')


def _validate_scn(payload: bytes, row: dict, root: Path) -> None:
    from solution.script.scn import collect_records, parse_translation, apply_translations
    relative = Path(row['resource']).relative_to('scn').with_suffix('.json')
    is_named = (root / 'solution/unpack/resource_names.json').is_file()
    cache = root / ('solution/script/work/scn_json' if is_named else 'solution/unpack/work/data/scn_json')
    path = cache / relative
    _plain(path, root)
    document = json.loads(path.read_text('utf-8-sig'))
    records = collect_records(document, relative.as_posix())
    values = parse_translation(payload.decode('utf-8-sig'), records)
    apply_translations(document, records, values)


def _system_guard(document: dict, baseline: dict) -> None:
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != 'translation'}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    if strip(document) != strip(baseline):
        raise ValueError('Returned system JSON changes fields other than translation')


def _project_file(root: Path, relative: str) -> Path:
    path = root / safe_name(relative)
    _plain(path, root)
    if not path.is_file():
        raise ValueError(f'Missing local input: {relative}')
    return path


def _input_files(stage: Path) -> dict:
    result = {}
    for subtree in ('translated', 'static'):
        for path in sorted((stage / subtree).rglob('*')):
            _plain(path, stage)
            if path.is_file():
                result[path.relative_to(stage).as_posix()] = sha(path.read_bytes())
    result['static_manifest.txt'] = sha((stage / 'static_manifest.txt').read_bytes())
    return result


def build_inputs(stage: Path, *, root: Path = ROOT) -> dict[str, Path]:
    root, stage = Path(root).absolute(), Path(stage).absolute()
    if '..' in root.parts or '..' in stage.parts:
        raise ValueError('Parent traversal is not allowed in handoff paths')
    allowed = root / 'solution/patch/work'
    if not stage.is_relative_to(allowed) or stage == allowed:
        raise ValueError('Handoff candidate must be inside solution/patch/work')
    _plain(stage, root)
    summary = json.loads((stage / 'handoff_summary.json').read_text('utf-8'))
    if summary.get('schema_version') != 1 or summary.get('project') != root.name:
        raise ValueError('Handoff candidate belongs to a different project')
    wanted = {'translation_dir': 'translated/scn', 'system_translation': 'translated/system/system.json',
              'static_root': 'static', 'static_manifest': 'static_manifest.txt'}
    if summary.get('inputs') != wanted:
        raise ValueError('Handoff build input paths were changed')
    for relative, expected in summary.get('input_files', {}).items():
        path = _project_file(stage, relative)
        if sha(path.read_bytes()) != expected:
            raise ValueError(f'Handoff candidate input was modified: {relative}')
    actual = _input_files(stage)
    if actual != summary.get('input_files'):
        raise ValueError('Handoff candidate input file coverage changed')
    return {key: stage / relative for key, relative in wanted.items()}


def prepare_return(source: Path, catalog_path: Path, output: Path | None = None, *, password: bytes | None = None,
                   reviewed_system: bool = False, replace_static: bool = False, root: Path = ROOT) -> dict:
    from solution.patch.handoff_catalog import validate_catalog
    from solution.patch import native_images, images
    from solution.script.system_text import rebuild as rebuild_system

    root = Path(root).absolute()
    catalog = json.loads(Path(catalog_path).read_text('utf-8-sig'))
    validate_catalog(catalog, root=root)
    stage = Path(output).absolute() if output else root / 'solution/patch/work' / ('return_' + uuid.uuid4().hex[:12])
    if '..' in stage.parts or not stage.resolve().is_relative_to((root / 'solution/patch/work').resolve()):
        raise ValueError('Return output escapes the project work directory')
    if not stage.is_relative_to(root / 'solution/patch/work') or stage == root / 'solution/patch/work':
        raise ValueError('Return output must be a new directory inside solution/patch/work')
    if stage.exists() or stage.is_symlink():
        raise ValueError('Return output already exists')
    _plain(stage.parent, root)
    stage.mkdir()
    translated = stage / 'translated'
    for subtree in ('scn', 'system'):
        (translated / subtree).mkdir(parents=True)
    formal = root / 'game_script/translated_script'
    for file in (formal / 'scn').rglob('*.txt'):
        _plain(file, root)
        target = translated / 'scn' / file.relative_to(formal / 'scn')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
    shutil.copy2(_project_file(root, 'game_script/translated_script/system/system.json'), translated / 'system/system.json')
    static = stage / 'static'
    static.mkdir()
    static_records = {}
    formal_static = root / 'solution/patch/base/chs_patch'
    formal_manifest = root / 'solution/patch/base/chs_patch_manifest.txt'
    for line in formal_manifest.read_text('utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name = _name(line.strip())
        file = _project_file(root, (formal_static / name).relative_to(root).as_posix())
        add_static(static, name, file.read_bytes(), static_records)
    changes, skipped, received = [], [], 0
    pimg_groups, cursor_groups = defaultdict(list), defaultdict(list)
    reviewed_ids = []
    try:
        for name, payload in _return_entries(source, catalog, password):
            received += 1
            row = catalog['files'][name]
            kind = row['kind']
            if kind == 'note':
                continue
            if sha(payload) == row['baseline_sha256']:
                skipped.append(name)
                continue
            if kind in ('scn', 'system'):
                baseline = _project_file(root, row['baseline_path'])
                if sha(baseline.read_bytes()) not in (row['baseline_sha256'], sha(payload)):
                    raise ValueError(f'Local translation changed after handoff; review conflict: {name}')
                if kind == 'scn':
                    _scn_guard(payload, baseline)
                    _validate_scn(payload, row, root)
                    destination = translated / name.removeprefix('文本/')
                    destination.write_bytes(payload)
                else:
                    document = json.loads(payload.decode('utf-8-sig'))
                    _system_guard(document, json.loads(baseline.read_text('utf-8-sig')))
                    document, reviewed_ids = review_system_edits(document, reviewed_system)
                    data_root = root / ('solution/unpack/work/named/data' if (root / 'solution/unpack/resource_names.json').is_file() else 'solution/unpack/work/data')
                    rebuild_system(document, data_root)
                    (translated / 'system/system.json').write_text(json.dumps(document, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                changes.append({'delivery': name, 'kind': kind})
                continue
            if not image_changed(payload, row, name):
                skipped.append(name)
                continue
            incoming = stage / 'incoming' / name
            incoming.parent.mkdir(parents=True, exist_ok=True)
            incoming.write_bytes(payload)
            original = _project_file(root, row['source']['path'])
            if kind == 'pimg':
                pimg_groups[row['resource']].append((name, row, incoming))
            elif kind == 'cursor':
                cursor_groups[row['resource']].append((name, row, incoming))
            elif kind == 'png':
                add_static(static, row['resource'], payload, static_records, replace=replace_static)
                changes.append({'delivery': name, 'kind': kind, 'resource': row['resource']})
            elif kind in ('tlg', 'jpeg'):
                output_file = stage / 'encoded' / row['archive'] / row['resource']
                encoder = native_images.encode_tlg if kind == 'tlg' else native_images.encode_jpeg
                result = encoder(incoming, output_file, original)
                add_static(static, row['resource'], output_file.read_bytes(), static_records, replace=replace_static)
                changes.append({'delivery': name, 'kind': kind, 'resource': row['resource'], 'verification': result})
            else:
                raise ValueError(f'Unsupported handoff file kind: {kind}')
        for resource, edits in cursor_groups.items():
            original = _project_file(root, edits[0][1]['source']['path'])
            indices = {row['frame_index']: path for _, row, path in edits}
            if len(indices) != len(edits):
                raise ValueError('Duplicate returned cursor frame')
            output_file = stage / 'encoded' / edits[0][1]['archive'] / resource
            result = native_images.rebuild_cursor(original, indices, output_file)
            add_static(static, resource, output_file.read_bytes(), static_records, replace=replace_static)
            changes.append({'kind': 'cursor', 'resource': resource, 'delivery': [name for name, _, _ in edits], 'verification': result})
        edit_root = stage / 'pimg'
        for resource, edits in pimg_groups.items():
            asset = resource
            original_asset = root / edits[0][1]['pimg']['asset']
            _plain(original_asset, root)
            for path in original_asset.rglob('*'):
                _plain(path, root)
            shutil.copytree(original_asset, edit_root / asset)
            prefix = '图片/' + edits[0][1]['archive'] + '/' + asset + '/'
            for name, row, path in edits:
                if not name.startswith(prefix):
                    raise ValueError('PIMG resource mapping is inconsistent')
                destination = edit_root / asset / safe_name(name[len(prefix):])
                if not destination.is_file():
                    raise ValueError(f'Missing preserved PIMG resource: {name}')
                shutil.copy2(path, destination)
        if pimg_groups:
            assets = sorted(pimg_groups)
            result = images.build_images(edit_root, stage / 'pimg_build', root / 'tools/Ulysses-FreeMoteToolkit-v4.3.0', assets)
            for item in result['files']:
                if item['changed']:
                    add_static(static, item['path'], Path(item['output']).read_bytes(), static_records, replace=replace_static)
                    changes.append({'kind': 'pimg', 'resource': item['path'], 'verification': item})
        (stage / 'static_manifest.txt').write_text(''.join(name+'\n' for name in sorted(static_records)), encoding='utf-8')
        summary = {'schema_version': 1, 'project': root.name, 'received_files': received,
                   'changed': changes, 'unchanged': skipped, 'reviewed_system_fields': reviewed_ids,
                   'static_files': static_records,
                   'inputs': {'translation_dir': 'translated/scn', 'system_translation': 'translated/system/system.json',
                              'static_root': 'static', 'static_manifest': 'static_manifest.txt'},
                   'input_files': _input_files(stage)}
        (stage / 'handoff_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        build_inputs(stage, root=root)
        return dict(summary, output=str(stage))
    except BaseException as error:
        (stage / 'import_error.txt').write_text(str(error)+'\n', encoding='utf-8')
        raise
