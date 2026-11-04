"""Bind an actual handoff ZIP to verified original resources and baseline pixels."""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import struct
import zipfile

from PIL import Image

from solution.patch import images
from solution.script.psb_shell import unwrap_psb


ROOT = Path(__file__).resolve().parents[2]
NOTE_NAME = '注意事项（请一定要看且严格遵守）.txt'
PROJECT = 'dc5_to4_chineseization'
TITLE = 'D.C.5 to 4 Luminous Memories'
HASH = re.compile(r'[0-9a-f]{64}\Z')
DEVICE = re.compile(r'(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?', re.IGNORECASE)
INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


class CatalogError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _load(path: Path):
    return json.loads(path.read_text('utf-8-sig'))


def _no_links(path: Path) -> None:
    for part in (path.absolute(), *path.absolute().parents):
        if not part.exists() and not part.is_symlink():
            continue
        attrs = part.lstat()
        if part.is_symlink() or getattr(attrs, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise CatalogError(f'Symlink or reparse point is not supported: {part}')


def _relative(value: str, *, hidden: bool = False) -> str:
    if not isinstance(value, str) or not value or value.startswith('/') or '\\' in value:
        raise CatalogError(f'Invalid path: {value!r}')
    for part in value.split('/'):
        if not part or part in ('.', '..') or part.endswith((' ', '.')) or INVALID.search(part) or DEVICE.fullmatch(part) or (not hidden and part.startswith('.')):
            raise CatalogError(f'Invalid or hidden path: {value!r}')
    return value


def _psb_array(data: bytes, offset: int) -> list[int]:
    if not 0 <= offset < len(data):
        raise CatalogError('PSB array offset is outside source')
    count_width = data[offset] - 12
    offset += 1
    if not 1 <= count_width <= 4 or offset + count_width >= len(data):
        raise CatalogError('Invalid PSB array count')
    count = int.from_bytes(data[offset:offset + count_width], 'little')
    offset += count_width
    width = data[offset] - 12
    offset += 1
    if not 1 <= width <= 4 or count > 1_000_000 or offset + count * width > len(data):
        raise CatalogError('Invalid PSB array values')
    return [int.from_bytes(data[at:at + width], 'little') for at in range(offset, offset + count * width, width)]


def _file(root: Path, relative: str) -> Path:
    relative = _relative(relative, hidden=True)
    root = root.absolute()
    _no_links(root)
    path = root / relative
    for part in (root, *(root / Path(*Path(relative).parts[:n]) for n in range(1, len(Path(relative).parts) + 1))):
        try:
            attrs = part.lstat()
        except FileNotFoundError as error:
            raise CatalogError(f'Missing source file: {path}') from error
        if part.is_symlink() or getattr(attrs, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise CatalogError(f'Symlink or reparse point is not supported: {part}')
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise CatalogError(f'Invalid source file: {path}')
    return path


def _source(root: Path, row: dict) -> dict:
    path = _file(root, row['source_path'])
    if _sha(path) != row['sha256']:
        raise CatalogError(f'Source SHA256 is stale: {row["source_path"]}')
    if row.get('raw_path'):
        raw = _file(root, row['raw_path'])
        if _sha(raw) != row['sha256']:
            raise CatalogError(f'Raw source SHA256 is stale: {row["raw_path"]}')
    return {'path': row['source_path'], 'sha256': row['sha256']}


def source_catalog(root: Path = ROOT) -> tuple[str, dict[tuple[str, str], dict]]:
    """Read authoritative resource identities; never infer names from work caches."""
    root = Path(root)
    work = 'solution/unpack/work/'
    name_map = root / 'solution/unpack/resource_names.json'
    result = {}
    if name_map.is_file():
        report = _load(_file(root, 'solution/unpack/resource_names.json'))
        project = report.get('project')
        if project != PROJECT:
            raise CatalogError(f'Wrong resource map project: {project}')
        manifests = {}
        for row in report['entries']:
            if row.get('status') != 'resolved':
                if row.get('status') == 'unknown' and row.get('content_kind') in ('png', 'tlg', 'jpeg', 'pimg', 'cur', 'bmp'):
                    raise CatalogError('Unresolved original image resource')
                continue
            archive = _relative(row['archive'])
            resource = _relative(row['package_path'])
            if archive not in manifests:
                manifest = _load(_file(root, work + f'manifests/{archive}.json'))
                if manifest.get('project') != project:
                    raise CatalogError('Wrong unpack manifest project')
                records = manifest['entries']
                manifests[archive] = {item['ordinal']: item for item in records}
                if len(manifests[archive]) != len(records):
                    raise CatalogError('Duplicate unpack manifest identity')
            original = manifests[archive].get(row['ordinal'])
            if not original or any(original.get(key) != row.get(key) for key in ('archive', 'raw_path', 'sha256', 'bytes')):
                raise CatalogError(f'Resource does not match original unpack manifest: {archive}/{resource}')
            record = dict(row, source_path=work + f'named/{archive}/{resource}', raw_path=work + row['raw_path'])
            identity = archive, resource
            if identity in result:
                raise CatalogError(f'Duplicate resource mapping: {identity}')
            result[identity] = record
    else:
        project = 'dc4_fd_chineseization'
        paths = sorted((root / work).glob('*_manifest.json'))
        if not paths:
            raise CatalogError('No authoritative original resource manifests')
        for path in paths:
            archive = _relative(path.name.removesuffix('_manifest.json'))
            manifest = _load(_file(root, path.relative_to(root).as_posix()))
            for row in manifest['files']:
                resource = _relative(row['name'])
                identity = archive, resource
                if identity in result:
                    raise CatalogError(f'Duplicate resource mapping: {identity}')
                result[identity] = dict(row, archive=archive, source_path=work + f'{archive}/{resource}')
    folded = [(archive.casefold(), resource.casefold()) for archive, resource in result]
    if len(set(folded)) != len(folded):
        raise CatalogError('Case-colliding original resource mapping')
    for row in result.values():
        if not isinstance(row.get('sha256'), str) or not HASH.fullmatch(row['sha256']):
            raise CatalogError('Invalid original resource SHA256')
    return project, result


def _pixels(data: bytes) -> dict:
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise CatalogError('Delivery image must be a PNG')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
            raise CatalogError('Delivery image must be a single PNG image')
        rgba = image.convert('RGBA')
        return {'width': rgba.width, 'height': rgba.height, 'rgba_sha256': _digest(rgba.tobytes())}


def _pimg_document(plain: bytes, structure_end: int) -> dict:
    """Read the PSB name trie and value tree to check retained PIMG structure."""
    def array(at):
        values = _psb_array(plain, at)
        width = plain[at] - 12
        end = at + 1 + width + 1 + len(values) * (plain[at + 1 + width] - 12)
        if end > structure_end:
            raise CatalogError('PIMG structure array crosses resource table')
        return values, end

    names_at, strings_at, strings_base = struct.unpack_from('<3I', plain, 12)
    alphabet, at = array(names_at)
    parents, at = array(at)
    indexes, _ = array(at)
    if len(alphabet) != len(parents):
        raise CatalogError('PIMG name trie arrays differ')
    names = []
    for index in indexes:
        chars, seen = [], set()
        if not 0 <= index < len(parents):
            raise CatalogError('PIMG name index is outside trie')
        index = parents[index]
        while index:
            if index in seen or not 0 <= index < len(parents):
                raise CatalogError('PIMG name trie is cyclic or out of bounds')
            seen.add(index)
            parent = parents[index]
            if not 0 <= parent < len(alphabet):
                raise CatalogError('PIMG name trie parent is out of bounds')
            value = index - alphabet[parent]
            if not 1 <= value <= 255:
                raise CatalogError('PIMG name trie byte is invalid')
            chars.append(value)
            index = parent
        names.append(bytes(reversed(chars)).decode('utf-8'))
    if len(set(names)) != len(names):
        raise CatalogError('PIMG structure contains duplicate name keys')
    strings, _ = array(strings_at)
    active = set()

    def read(at, depth=0):
        if not 40 <= at < structure_end or at in active or depth > 256:
            raise CatalogError('PIMG value tree is cyclic or outside structure')
        active.add(at)
        try:
            tag = plain[at]
            if tag in (0, 1):
                return None
            if tag in (2, 3):
                return tag == 3
            if tag == 4:
                return 0
            if 5 <= tag <= 12:
                width = tag - 4
                if at + 1 + width > structure_end:
                    raise CatalogError('Truncated PIMG integer')
                return int.from_bytes(plain[at + 1:at + 1 + width], 'little', signed=True)
            if 0x15 <= tag <= 0x1c:
                width = (tag - 0x15) % 4 + 1
                if at + 1 + width > structure_end:
                    raise CatalogError('Truncated PIMG reference')
                index = int.from_bytes(plain[at + 1:at + 1 + width], 'little')
                if tag >= 0x19:
                    return f'#resource#{index}'
                if index >= len(strings):
                    raise CatalogError('PIMG string index is out of bounds')
                start = strings_base + strings[index]
                end = plain.find(b'\0', start, structure_end)
                if not strings_base <= start < structure_end or end < start:
                    raise CatalogError('PIMG string is out of bounds')
                return plain[start:end].decode('utf-8')
            if tag == 0x1d:
                return 0.0
            if tag in (0x1e, 0x1f):
                width = 4 if tag == 0x1e else 8
                if at + 1 + width > structure_end:
                    raise CatalogError('Truncated PIMG float')
                return struct.unpack_from('<f' if width == 4 else '<d', plain, at + 1)[0]
            if tag == 0x20:
                offsets, base = array(at + 1)
                return [read(base + offset, depth + 1) for offset in offsets]
            if tag == 0x21:
                keys, next_at = array(at + 1)
                offsets, base = array(next_at)
                if len(keys) != len(offsets) or len(keys) != len(set(keys)) or any(key >= len(names) for key in keys):
                    raise CatalogError('Invalid PIMG object keys')
                return {names[key]: read(base + offset, depth + 1) for key, offset in zip(keys, offsets)}
            raise CatalogError(f'Unsupported PIMG structure value type: {tag:#x}')
        finally:
            active.remove(at)

    document = read(struct.unpack_from('<I', plain, 36)[0])
    if not isinstance(document, dict):
        raise CatalogError('Source PIMG root is not an object')
    return document


def _pimg_structure(data: bytes, baseline: dict) -> dict:
    plain, shell = unwrap_psb(data)
    if len(plain) < 44:
        raise CatalogError('Truncated source PIMG structure')
    offsets_at, lengths_at, resource_base, root_at = struct.unpack_from('<4I', plain, 24)
    if not 40 <= offsets_at < lengths_at < resource_base <= len(plain) or not 40 <= root_at < offsets_at:
        raise CatalogError('Invalid source PIMG resource boundaries')
    offsets, lengths = _psb_array(plain, offsets_at), _psb_array(plain, lengths_at)
    if len(offsets) != len(lengths) or not offsets:
        raise CatalogError('Invalid source PIMG resource table')
    spans = []
    for offset, length in zip(offsets, lengths):
        if length <= 0 or offset < 0 or resource_base + offset + length > len(plain):
            raise CatalogError('Invalid source PIMG resource span')
        spans.append((offset, offset + length))
    ordered = sorted(spans)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise CatalogError('Overlapping source PIMG resources')
    indices = baseline['resource_indices']
    expected = [int(value.removeprefix('#resource#')) for value in indices.values() if images.RESOURCE.fullmatch(value)]
    if len(expected) != len(indices) or set(expected) != set(range(len(offsets))):
        raise CatalogError('PIMG retained resource indices differ from source container')
    if plain[root_at] != 0x21:
        raise CatalogError('Source PIMG root is not an object')

    def array_end(at):
        width = plain[at] - 12
        values = _psb_array(plain, at)
        return at + 1 + width + 1 + len(values) * (plain[at + 1 + width] - 12)

    keys_at = root_at + 1
    keys = _psb_array(plain, keys_at)
    values_at = array_end(keys_at)
    values = _psb_array(plain, values_at)
    base = array_end(values_at)
    if len(keys) != len(values) or len(keys) != len(baseline['structure']):
        raise CatalogError('PIMG root structure differs from retained structure')
    actual = []
    for offset in values:
        at = base + offset
        if not base <= at < offsets_at:
            raise CatalogError('PIMG object value is outside structure')
        tag = plain[at]
        if 0x19 <= tag <= 0x1c:
            width = tag - 0x18
            if at + 1 + width > offsets_at:
                raise CatalogError('Truncated PIMG resource index')
            actual.append(int.from_bytes(plain[at + 1:at + 1 + width], 'little'))
    if actual != expected:
        raise CatalogError('PIMG source resource indices differ from retained resource keys')
    document = _pimg_document(plain, offsets_at)
    references = {}

    def normalize(value, pointer=''):
        if isinstance(value, dict):
            return {key: normalize(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1')) for key, child in value.items()}
        if isinstance(value, list):
            return [normalize(child, pointer + '/' + str(index)) for index, child in enumerate(value)]
        if isinstance(value, str) and (match := images.RESOURCE.fullmatch(value)):
            references.setdefault(match.group(1), []).append(pointer)
            return '#resource#'
        return value

    structure = normalize(document)
    groups = {pointer: sorted(pointers) for pointers in references.values() for pointer in pointers}
    source_indices = {key: value for key, value in document.items() if isinstance(value, str) and images.RESOURCE.fullmatch(value)}
    if structure != baseline['structure'] or groups != baseline['references'] or source_indices != baseline['resource_indices']:
        raise CatalogError('PIMG source structure, resource keys or reference relationships differ from retained baseline')
    return {'shell': shell, 'resource_count': len(offsets), 'structure_sha256': _digest(plain[:resource_base])}


def _expected(root: Path) -> tuple[str, dict[str, dict]]:
    project, sources = source_catalog(root)
    expected = {}
    folded = set()
    native = []

    def add(name, row):
        _relative(name)
        if name in expected or name.casefold() in folded:
            raise CatalogError(f'Duplicate delivery mapping: {name}')
        folded.add(name.casefold())
        expected[name] = dict(row, delivery=name)

    for (archive, resource), original in sources.items():
        suffix = Path(resource).suffix.lower()
        if suffix not in ('.png', '.tlg', '.jpg', '.jpeg', '.pimg', '.cur', '.scn'):
            if suffix in ('.bmp', '.gif', '.dds', '.webp'):
                raise CatalogError(f'Unsupported original image type: {archive}/{resource}')
            continue
        source = _source(root, original)
        common = {'archive': archive, 'resource': resource, 'source': source}
        if suffix == '.png':
            payload = _file(root, source['path']).read_bytes()
            add(f'图片/{archive}/{resource}', dict(common, kind='png', baseline_sha256=_digest(payload), **_pixels(payload)))
        elif suffix in ('.tlg', '.jpg', '.jpeg'):
            name = f'图片/{archive}/{resource}.png'
            add(name, dict(common, kind='tlg' if suffix == '.tlg' else 'jpeg'))
            native.append((name, _file(root, source['path'])))
        elif suffix == '.scn':
            if archive != 'data' or not resource.startswith('scn/'):
                continue
            relative = 'game_script/translated_script/' + resource.removesuffix('.scn') + '.txt'
            path = _file(root, relative)
            add('文本/' + resource.removesuffix('.scn') + '.txt', dict(common, kind='scn', baseline_path=relative, baseline_sha256=_sha(path)))
        elif suffix == '.cur':
            from solution.patch.native_images import read_cursor
            cursor = read_cursor(_file(root, source['path']))
            if cursor['file_sha256'] != source['sha256'] or cursor['frame_count'] != len(cursor['frames']):
                raise CatalogError('Cursor source identity mismatch')
            for index, frame in enumerate(cursor['frames']):
                if frame['index'] != index:
                    raise CatalogError('Non-sequential source cursor frames')
                name = f'图片/{archive}/{resource}/frame_{index:02}_{frame["width"]}x{frame["height"]}.png'
                add(name, dict(common, kind='cursor', frame_index=index, **{key: frame[key] for key in ('width', 'height', 'rgba_sha256', 'hotspot_x', 'hotspot_y', 'frame_sha256')}))
        else:
            if archive != 'data':
                raise CatalogError('Unsupported PIMG archive')
            asset_relative = 'solution/patch/work/images/' + resource
            metadata_relative = asset_relative + '/' + images.METADATA
            metadata_file = _file(root, metadata_relative)
            metadata = _load(metadata_file)
            if metadata.get('source_path') != resource or metadata.get('source_sha256') != source['sha256']:
                raise CatalogError(f'PIMG retained baseline has stale source identity: {resource}')
            asset = metadata_file.parent
            baseline = metadata['baseline']
            current = images.snapshot(asset)
            images.compare_snapshots(baseline, current)
            if baseline['resource_indices'] != current['resource_indices']:
                raise CatalogError('PIMG retained resource indices changed')
            structural = _pimg_structure(_file(root, source['path']).read_bytes(), baseline)
            if structural['shell'] != metadata.get('shell', {'type': 'none'}):
                raise CatalogError('PIMG original shell differs from retained baseline')
            docs = [path for path in asset.glob('*.json') if not path.name.startswith('.') and not path.name.endswith('.resx.json')]
            document = docs[0]
            resx = document.with_suffix('.resx.json')
            resources = _load(resx)['Resources']
            shared = {'asset': asset_relative, 'metadata_sha256': _sha(metadata_file), 'document_sha256': _sha(document),
                      'resx_sha256': _sha(resx), 'source_structure': structural}
            for key, pixels in baseline['images'].items():
                relative = _relative(resources[Path(key).stem])
                cached = _file(root, asset_relative + '/' + relative)
                details = dict(shared, resource_key=key, resource_index=baseline['resource_indices'][key])
                add(f'图片/{archive}/{resource}/{relative}', dict(common, kind='pimg', pimg=details, baseline_sha256=_sha(cached), **pixels))
    if native:
        from solution.patch.native_images import read_rgba_many
        decoded = read_rgba_many(path for _, path in native)
        for name, path in native:
            actual = decoded[str(path)]
            if actual['file_sha256'] != expected[name]['source']['sha256']:
                raise CatalogError('Native image source identity mismatch')
            expected[name].update({key: actual[key] for key in ('width', 'height', 'rgba_sha256')})
    system_path = 'game_script/translated_script/system/system.json'
    raw_path = 'game_script/raw_script/system/system.json'
    system = _load(_file(root, raw_path))
    system_sources = []
    for resource, identity in sorted(system['source_files'].items()):
        if ('data', resource) not in sources or sources['data', resource]['sha256'] != identity['sha256']:
            raise CatalogError(f'System source does not match original resource: {resource}')
        system_sources.append(_source(root, sources['data', resource]))
    add('文本/system/system.json', {'kind': 'system', 'source': {'path': raw_path, 'sha256': _sha(_file(root, raw_path))},
                                'sources': system_sources, 'baseline_path': system_path, 'baseline_sha256': _sha(_file(root, system_path))})
    add(NOTE_NAME, {'kind': 'note'})
    scripts = {_relative(path.relative_to(root / 'game_script/translated_script').as_posix())
               for path in (root / 'game_script/translated_script/scn').glob('*.txt')}
    required_scripts = {name.removeprefix('文本/') for name, row in expected.items() if row['kind'] == 'scn'}
    if scripts != required_scripts:
        raise CatalogError('Scene text coverage differs from original source resources')
    return project, expected


def _zip(package: Path):
    try:
        import pyzipper
    except ImportError:
        return zipfile.ZipFile(package)
    return pyzipper.AESZipFile(package)


def build_catalog(package: Path, password: bytes, *, root: Path = ROOT) -> dict:
    root, package = Path(root).absolute(), Path(package).absolute()
    if not isinstance(password, bytes):
        raise CatalogError('ZIP password must be bytes')
    _no_links(package)
    project, expected = _expected(root)
    before = _sha(package)
    with _zip(package) as archive:
        members = archive.infolist()
        seen, folded = set(), set()
        for member in members:
            name = _relative(member.filename)
            mode = member.external_attr >> 16
            if member.external_attr & (2 | stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise CatalogError(f'ZIP member cannot have hidden or reparse attributes: {name}')
            if member.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                raise CatalogError(f'ZIP member must be a regular file: {name}')
            if name in seen or name.casefold() in folded:
                raise CatalogError(f'Duplicate or case-colliding ZIP member: {name}')
            seen.add(name)
            folded.add(name.casefold())
        if seen != set(expected):
            raise CatalogError(f'ZIP coverage mismatch; missing={sorted(set(expected)-seen)[:8]}, unknown={sorted(seen-set(expected))[:8]}')
        for member in members:
            name = member.filename
            row = expected[name]
            try:
                payload = archive.read(member, pwd=password)
            except NotImplementedError as error:
                raise CatalogError('AES ZIP support requires the pyzipper dependency') from error
            sha = _digest(payload)
            if 'baseline_sha256' in row and row['baseline_sha256'] != sha:
                raise CatalogError(f'Delivery baseline differs from verified source: {name}')
            if row['kind'] in ('png', 'tlg', 'jpeg', 'pimg', 'cursor'):
                actual = _pixels(payload)
                if any(row[key] != value for key, value in actual.items()):
                    raise CatalogError(f'Delivery pixels differ from verified source baseline: {name}')
            elif row['kind'] == 'note':
                title = TITLE if project == PROJECT else 'D.C.4 Fortunate Departures'
                if not payload.decode('utf-8-sig').startswith('《' + title + '》'):
                    raise CatalogError('Handoff note belongs to another project')
            row['baseline_sha256'] = sha
            row['bytes'] = len(payload)
    if _sha(package) != before:
        raise CatalogError('ZIP changed while building catalog')
    package_path = package.relative_to(root).as_posix() if package.is_relative_to(root) else str(package)
    return {'schema_version': 1, 'project': project,
            'package': {'name': package.name, 'path': package_path, 'sha256': before, 'bytes': package.stat().st_size},
            'counts': dict(sorted(Counter(row['kind'] for row in expected.values()).items())), 'files': dict(sorted(expected.items()))}


def write_catalog(package: Path, password: bytes, output: Path, *, root: Path = ROOT) -> dict:
    root = Path(root).absolute()
    output = Path(output).absolute()
    _no_links(output)
    protected = [root / relative for relative in ('game', 'game_script', 'solution/unpack', 'solution/patch/base', 'solution/patch/work/images')]
    if output.resolve() == Path(package).resolve() or any(output.resolve().is_relative_to(path.resolve()) for path in protected):
        raise CatalogError('Catalog output cannot overwrite handoff or original inputs')
    if output.suffix.lower() != '.json':
        raise CatalogError('Catalog output must be a JSON file')
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise CatalogError('Catalog output must be a regular file')
    result = build_catalog(package, password, root=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT for path in (output.parent, *output.parent.parents)):
        raise CatalogError('Catalog output cannot traverse a symlink or reparse point')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def validate_catalog(catalog: dict, root: Path = ROOT) -> None:
    root = Path(root).absolute()
    if not isinstance(catalog, dict) or catalog.get('schema_version') != 1:
        raise CatalogError('Unsupported handoff catalog schema')
    if set(catalog) != {'schema_version', 'project', 'package', 'counts', 'files'}:
        raise CatalogError('Unknown catalog fields')
    project, expected = _expected(root)
    if catalog.get('project') != project or not isinstance(catalog.get('files'), dict) or set(catalog['files']) != set(expected):
        raise CatalogError('Catalog project or file coverage differs from current sources')
    package = catalog.get('package', {})
    if not isinstance(package, dict) or set(package) != {'name', 'path', 'sha256', 'bytes'}:
        raise CatalogError('Invalid catalog ZIP identity')
    stored_path = package.get('path')
    if not isinstance(stored_path, str):
        raise CatalogError('Invalid catalog ZIP path')
    path = Path(stored_path)
    if not path.is_absolute():
        path = root / _relative(stored_path)
    _no_links(path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink() or path.name != package.get('name') or _sha(path) != package.get('sha256') or path.stat().st_size != package.get('bytes'):
        raise CatalogError('Catalog ZIP identity is stale')
    folded = set()
    for name, baseline in expected.items():
        _relative(name)
        if name.casefold() in folded:
            raise CatalogError('Case-colliding catalog paths')
        folded.add(name.casefold())
        row = catalog['files'][name]
        if not isinstance(row, dict) or any(row.get(key) != value for key, value in baseline.items()):
            raise CatalogError(f'Catalog source or pixel baseline identity differs: {name}')
        if not isinstance(row.get('baseline_sha256'), str) or not HASH.fullmatch(row['baseline_sha256']) or type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise CatalogError(f'Invalid catalog file baseline: {name}')
        if set(row) != set(baseline) | {'baseline_sha256', 'bytes'}:
            raise CatalogError(f'Unknown catalog file fields: {name}')
    if catalog.get('counts') != dict(sorted(Counter(row['kind'] for row in expected.values()).items())):
        raise CatalogError('Catalog type coverage differs')
