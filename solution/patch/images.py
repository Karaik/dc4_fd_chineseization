"""Editable DC4 FD PIMG assets with image and structure verified publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import uuid

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / 'solution/unpack/work/data'
DEFAULT_EDIT = ROOT / 'solution/patch/work/images'
DEFAULT_TOOLS = ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0'
DEFAULT_STATIC = ROOT / 'solution/patch/base/chs_patch'
DEFAULT_MANIFEST = ROOT / 'solution/patch/base/chs_patch_manifest.txt'
METADATA = '.pimg_asset.json'
RESOURCE = re.compile(r'^#resource#(\d+)$')


class ImageError(ValueError):
    pass


def load_json(path: Path):
    return json.loads(path.read_text('utf-8-sig'))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_path(value: str) -> str:
    posix = PurePosixPath(value.replace('\\', '/'))
    if not value or posix.is_absolute() or any(part in ('', '.', '..') or ':' in part for part in value.replace('\\', '/').split('/')):
        raise ImageError(f'Invalid resource path: {value}')
    return posix.as_posix()


def inside(path: Path, root: Path) -> Path:
    result = Path(path).resolve()
    if not result.is_relative_to(root.resolve()) or result == root.resolve():
        raise ImageError(f'Output must be strictly inside its project directory: {path}')
    for parent in (Path(path).absolute(), *Path(path).absolute().parents):
        if parent == root.resolve().parent:
            break
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ImageError(f'Symlink or junction is not supported: {parent}')
    return result


def work_output(path: Path) -> Path:
    path = inside(path, ROOT)
    allowed = ROOT / 'solution/patch/work'
    if path != allowed and not path.is_relative_to(allowed):
        raise ImageError(f'Image work output must be inside solution/patch/work: {path}')
    return path


def run_tool(tool: Path, args: list[str], cwd: Path, log: Path) -> None:
    if not tool.is_file():
        raise FileNotFoundError(f'FreeMote tool is missing: {tool}')
    result = subprocess.run([str(tool), *args], cwd=cwd, capture_output=True, encoding='utf-8', errors='replace', timeout=300)
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ImageError(f'FreeMote failed with code {result.returncode}; see {log}')


def snapshot(asset: Path) -> dict:
    asset = asset.resolve()
    documents = [p for p in asset.glob('*.json') if not p.name.endswith('.resx.json') and not p.name.startswith('.')]
    if len(documents) != 1:
        raise ImageError(f'Expected one editable PIMG JSON: {asset}')
    document = documents[0]
    doc = load_json(document)
    meta = load_json(document.with_suffix('.resx.json'))
    if meta.get('PsbType') != 'Pimg' or meta.get('PsbVersion') != 3 or not isinstance(doc.get('layers'), list):
        raise ImageError(f'Unsupported PIMG metadata: {document}')
    resources = meta.get('Resources')
    if not isinstance(resources, dict):
        raise ImageError('PIMG Resources must be an object')
    references: dict[str, list[str]] = {}

    def walk(value, pointer=''):
        if isinstance(value, dict):
            return {key: walk(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1')) for key, child in value.items()}
        if isinstance(value, list):
            return [walk(child, pointer + '/' + str(index)) for index, child in enumerate(value)]
        if isinstance(value, str) and (match := RESOURCE.fullmatch(value)):
            references.setdefault(match.group(1), []).append(pointer)
            return '#resource#'
        return value

    structure = walk(doc)
    groups = {pointer: sorted(pointers) for pointers in references.values() for pointer in pointers}
    images = {}
    resource_indices = {}
    for key, value in doc.items():
        if not isinstance(value, str) or not RESOURCE.fullmatch(value):
            continue
        name = Path(key).stem
        if name not in resources:
            raise ImageError(f'PIMG resource mapping is missing: {key}')
        relative = relative_path(resources[name])
        target = inside(asset / relative, asset)
        if not target.is_file() or target.suffix.lower() != '.png':
            raise ImageError(f'Expected exported PNG resource: {relative}')
        with Image.open(target) as image:
            rgba = image.convert('RGBA')
            images[key] = {'width': rgba.width, 'height': rgba.height, 'rgba_sha256': hashlib.sha256(rgba.tobytes()).hexdigest()}
        resource_indices[key] = value
    if set(resources) != {Path(key).stem for key in images}:
        raise ImageError('PIMG resource entries do not match the document')
    metadata = {key: value for key, value in meta.items() if key != 'Resources'}
    return {'structure': structure, 'references': groups, 'images': images, 'metadata': metadata, 'resource_indices': resource_indices}


def compare_snapshots(expected: dict, actual: dict) -> None:
    if expected['references'] != actual['references']:
        raise ImageError('PIMG resource reference relationships differ')
    if expected['structure'] != actual['structure'] or expected['metadata'] != actual['metadata']:
        raise ImageError('PIMG structure or layer coordinates differ')
    if set(expected['images']) != set(actual['images']):
        raise ImageError('PIMG resource image keys differ')
    for key, value in expected['images'].items():
        if value != actual['images'][key]:
            raise ImageError(f'PIMG pixels or dimensions differ: {key}')


def select_sources(source: Path, selected: list[str] | None) -> list[tuple[str, Path]]:
    if selected is None:
        selected = sorted(p.relative_to(source).as_posix() for p in source.rglob('*.pimg'))
    paths = []
    seen = set()
    for value in selected:
        relative = relative_path(value)
        if not relative.endswith('.pimg') or relative in seen:
            raise ImageError(f'Invalid or duplicate PIMG selection: {relative}')
        seen.add(relative)
        path = inside(source / relative, source)
        if not path.is_file():
            raise FileNotFoundError(f'PIMG is missing: {path}')
        paths.append((relative, path))
    if not paths:
        raise ImageError('No PIMG inputs were selected')
    return paths


def decompile(source: Path, target: Path, tools: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    run_tool(tools / 'PsbDecompile.exe', ['-t', 'Pimg', '-o', str(target), str(source)], target, target / '.decompile.log')
    return snapshot(target)


def export_images(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_EDIT, tools: Path = DEFAULT_TOOLS,
                  selected: list[str] | None = None, *, force: bool = False) -> dict:
    source, output, tools = source.resolve(), work_output(output), tools.resolve()
    inputs = select_sources(source, selected)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for relative, path in inputs:
        target = inside(output / relative, output)
        checksum = sha256(path)
        backup = None
        if target.exists():
            previous = target / METADATA
            if not force:
                if not previous.is_file() or load_json(previous).get('source_sha256') != checksum:
                    raise ImageError(f'Existing image input has no matching baseline; use --force with backup: {target}')
                results.append({'path': relative, 'asset': str(target), 'preserved': True})
                continue
            backup = output.parent / ('images_backup_' + uuid.uuid4().hex[:12]) / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup)
        stage = output.parent / ('image_export_' + uuid.uuid4().hex[:12]) / relative
        baseline = decompile(path, stage, tools)
        write_json(stage / METADATA, {'format': 'dc4-pimg-v1', 'source_path': relative, 'source_sha256': checksum,
            'decompiler_sha256': sha256(tools / 'PsbDecompile.exe'), 'baseline': baseline})
        shutil.copytree(stage, target, dirs_exist_ok=True)
        results.append({'path': relative, 'asset': str(target), 'preserved': False, 'resources': len(baseline['images']), 'backup': str(backup) if backup else None})
    report = {'files': results, 'exported': sum(not item['preserved'] for item in results), 'preserved': sum(item['preserved'] for item in results)}
    write_json(output / '.export_report.json', report)
    return report


def _assets(edit_root: Path, selected: list[str] | None) -> list[tuple[str, Path]]:
    if selected is None:
        selected = sorted(p.parent.relative_to(edit_root).as_posix() for p in edit_root.rglob(METADATA))
    result = []
    seen = set()
    for relative in selected:
        relative = relative_path(relative)
        asset = inside(edit_root / relative, edit_root)
        if relative in seen or not (asset / METADATA).is_file():
            raise ImageError(f'Unknown or duplicate editable PIMG: {relative}')
        if load_json(asset / METADATA).get('source_path') != relative:
            raise ImageError(f'Editable PIMG path differs from baseline: {relative}')
        seen.add(relative)
        result.append((relative, asset))
    if not result:
        raise ImageError('No editable PIMG assets were selected')
    return result


def build_images(edit_root: Path, output: Path, tools: Path = DEFAULT_TOOLS, selected: list[str] | None = None) -> dict:
    edit_root, output, tools = work_output(edit_root), work_output(output), tools.resolve()
    if output == edit_root or output.is_relative_to(edit_root) or edit_root.is_relative_to(output):
        raise ImageError('Build output must be separate from editable inputs')
    if output.exists() and any(output.iterdir()):
        raise ImageError(f'PIMG build output must be empty: {output}')
    assets = _assets(edit_root, selected)
    prepared = [(relative, asset, snapshot(asset), load_json(asset / METADATA)['baseline']) for relative, asset in assets]
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for relative, asset, expected, baseline in prepared:
        job = output / '.work' / relative
        compiled, checked = job / 'compiled', job / 'verified'
        compiled.mkdir(parents=True)
        documents = [p for p in asset.glob('*.json') if not p.name.endswith('.resx.json') and not p.name.startswith('.')]
        document = documents[0]
        run_tool(tools / 'PsBuild.exe', [str(document)], compiled, job / 'compile.log')
        generated = compiled / (document.stem + '.pure.pimg')
        if not generated.is_file() or not generated.read_bytes().startswith(b'PSB\0\x03\0\0\0'):
            raise ImageError(f'FreeMote did not produce expected PSB v3 PIMG: {generated}')
        target = inside(output / relative, output)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated, target)
        actual = decompile(target, checked, tools)
        compare_snapshots(expected, actual)
        changed = False
        try:
            compare_snapshots(baseline, expected)
        except ImageError:
            changed = True
        results.append({'path': relative, 'output': str(target), 'verified_asset': str(checked), 'changed': changed,
            'resources': len(actual['images']), 'layers': len(actual['structure']['layers']),
            'binary_sha256': sha256(target), 'resource_indices_normalized': expected['resource_indices'] != actual['resource_indices']})
    report = {'files': results, 'verified': len(results), 'changed': sum(item['changed'] for item in results),
        'resource_images': sum(item['resources'] for item in results), 'layers': sum(item['layers'] for item in results),
        'compiler_sha256': sha256(tools / 'PsBuild.exe')}
    write_json(output / '.build_report.json', report)
    return report


def publish_images(edit_root: Path, tools: Path, selected: list[str], *, static_root: Path = DEFAULT_STATIC,
                   manifest: Path = DEFAULT_MANIFEST, force: bool = False) -> dict:
    if not selected:
        raise ImageError('Publishing requires an explicit PIMG selection')
    static_root, manifest = inside(static_root, ROOT), inside(manifest, ROOT)
    work = ROOT / 'solution/patch/work'
    if (static_root != DEFAULT_STATIC and not static_root.is_relative_to(work)) or (manifest != DEFAULT_MANIFEST and not manifest.is_relative_to(work)):
        raise ImageError('Invalid image publication output; use the static patch paths or an isolated work directory')
    report_root = ROOT / 'solution/patch/work' / ('image_publish_' + uuid.uuid4().hex[:12])
    built = build_images(edit_root, report_root, tools, selected)
    changes = [item for item in built['files'] if item['changed']]
    previous = manifest.read_text('utf-8-sig').splitlines() if manifest.is_file() else []
    for line in previous:
        if line.strip() and not line.lstrip().startswith('#'):
            relative_path(line.strip())
    for item in changes:
        destination = inside(static_root / item['path'], static_root)
        source = Path(item['output'])
        if destination.exists() and destination.read_bytes() != source.read_bytes() and not force:
            raise ImageError(f'Existing static PIMG differs; use --force with backup: {destination}')
    published = []
    for item in changes:
        destination = inside(static_root / item['path'], static_root)
        if destination.exists() and destination.read_bytes() != Path(item['output']).read_bytes():
            backup = report_root / 'previous_static' / item['path']
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item['output'], destination)
        if item['path'] not in previous:
            previous.append(item['path'])
        published.append(item['path'])
    if changes:
        if manifest.is_file():
            shutil.copy2(manifest, report_root / 'previous_manifest.txt')
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('\n'.join(previous) + '\n', encoding='utf-8')
    result = {'published': len(published), 'paths': published, 'verified': built['verified'], 'report': str(report_root / '.build_report.json')}
    write_json(report_root / '.publish_report.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Export, rebuild, verify and explicitly publish DC4 FD PIMG images.')
    parser.add_argument('--tools', type=Path, default=DEFAULT_TOOLS)
    subparsers = parser.add_subparsers(dest='command', required=True)
    export = subparsers.add_parser('export')
    export.add_argument('paths', nargs='*')
    export.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    export.add_argument('--output', type=Path, default=DEFAULT_EDIT)
    export.add_argument('--force', action='store_true')
    build = subparsers.add_parser('build')
    build.add_argument('paths', nargs='*')
    build.add_argument('--images', type=Path, default=DEFAULT_EDIT)
    build.add_argument('--output', type=Path)
    publish = subparsers.add_parser('publish')
    publish.add_argument('paths', nargs='+')
    publish.add_argument('--images', type=Path, default=DEFAULT_EDIT)
    publish.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if args.command == 'export':
        result = export_images(args.source, args.output, args.tools, args.paths or None, force=args.force)
    elif args.command == 'build':
        output = args.output or ROOT / 'solution/patch/work' / ('images_build_' + uuid.uuid4().hex[:12])
        result = build_images(args.images, output, args.tools, args.paths or None)
    else:
        result = publish_images(args.images, args.tools, args.paths, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
