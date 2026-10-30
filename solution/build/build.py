from __future__ import annotations

import argparse
import configparser
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from solution.build.build_cleanup import cleanup_previous_outputs, generated_output
from solution.runtime.build import build as build_runtime
from solution.script.scn import compile_translations, ensure_decompile, export_text
from solution.script.system_text import export as export_system, rebuild as rebuild_system
from solution.unpack.cli import DEFAULT_OUTPUT, unpack_archive
from solution.unpack.xp3 import extract_archive, pack_xp3, safe_name


ROOT = Path(__file__).resolve().parents[2]
BUILDER = 'dc4-fd-localization'
MUTABLE_RELEASE = ('savedata_chs/**', 'chs_patch/launcher.log', 'chs_patch/runtime.log')


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for data in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def static_resources(target: Path) -> list[str]:
    base = ROOT / 'solution/patch/base/chs_patch'
    manifest = base.parent / 'chs_patch_manifest.txt'
    names = [safe_name(line.strip()) for line in manifest.read_text('utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Duplicate static resource paths')
    for name in names:
        source = base / name
        if source.is_symlink() or not source.resolve().is_relative_to(base.resolve()) or not source.is_file():
            raise ValueError(f'Invalid static resource: {source}')
        if source.suffix.lower() in ('.exe', '.dll', '.xp3'):
            raise ValueError(f'Generated binary cannot be a static resource: {source}')
    for name in names:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / name, destination)
    return names


def add_scn_overrides(binaries: list[str], source_root: Path, patch: Path) -> None:
    for binary in binaries:
        source = Path(binary)
        relative = source.relative_to(source_root)
        destination = patch / 'scn' / relative
        if destination.exists():
            raise ValueError(f'Compiled script conflicts with a static override: {relative}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def runtime_assets(package: Path, config: str, assets: Path | None = None) -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(config)
    relative = parser.get('runtime', 'fontpath', fallback='').strip()
    if not relative:
        return []
    relative = safe_name(relative)
    if Path(relative).suffix.lower() not in ('.ttf', '.otf', '.ttc'):
        raise ValueError('Runtime fontpath must identify a TTF, OTF or TTC font')
    assets = assets or ROOT / 'solution/runtime/assets'
    source = assets / relative
    if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(assets.resolve()):
        raise ValueError(f'Missing or unsafe maintained runtime font: {source}')
    target = package / relative
    if target.exists():
        raise ValueError(f'Runtime font conflicts with an existing package file: {relative}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return [relative]


def smoke_translations(source: Path, target: Path) -> dict:
    shutil.copytree(source, target)
    path = target / '4fd_pro191025a1.ks.txt'
    lines = path.read_text('utf-8').splitlines()
    found = 0
    for index, line in enumerate(lines):
        if line.startswith('●13c5194a08ec5ce4f5d07533●'):
            prefix = line[:line.find('●', 1) + 1]
            lines[index] = prefix + json.dumps('「……没有忘带东西吧？中文显示测试。」', ensure_ascii=False)
            found += 1
    if found != 1:
        raise ValueError('The recorded Chinese smoke sample was not found exactly once')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'script':'scn/4fd_pro191025a1.ks.scn', 'record_id':'13c5194a08ec5ce4f5d07533', 'text':'「……没有忘带东西吧？中文显示测试。」'}


def verify_package_files(package: Path) -> dict:
    files = []
    for path in sorted(package.rglob('*')):
        if path.is_file() and path.name != '.vn-build-output.json':
            files.append({'path':path.relative_to(package).as_posix(), 'bytes':path.stat().st_size, 'sha256':digest(path)})
    return {'files':files, 'file_count':len(files)}


def _is_alive(pid: int) -> bool:
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    alive = bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
    kernel.CloseHandle(ctypes.c_void_p(handle))
    return alive


def stop_game(pid: int) -> None:
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
    if handle:
        kernel.TerminateProcess(ctypes.c_void_p(handle), 0)
        kernel.WaitForSingleObject(ctypes.c_void_p(handle), 5000)
        kernel.CloseHandle(ctypes.c_void_p(handle))


def read_log(path: Path) -> str:
    try:
        return path.read_text('utf-8', errors='replace')
    except (FileNotFoundError, PermissionError):
        return ''


def smoke_game(release: Path, leave_running: bool = False, timeout: float = 60) -> dict:
    launcher = release / 'DC4_FD_chs.exe'
    subprocess.run([str(launcher), '--check', '--no-dialog'], cwd=release, check=True, timeout=30)
    proc = subprocess.Popen([str(launcher), '--wait', '--no-dialog'], cwd=release)
    pid = None
    started = time.monotonic()
    launcher_log = release / 'chs_patch/launcher.log'
    runtime_log = release / 'chs_patch/runtime.log'
    reached = False
    success = False
    try:
        while time.monotonic() - started < timeout:
            text = read_log(launcher_log)
            match = re.search(r'PROCESS pid=(\d+) ', text)
            if match:
                pid = int(match[1])
            logs = read_log(runtime_log)
            if 'ERROR ' in logs:
                raise RuntimeError(f'Runtime initialization or loading failed; inspect {runtime_log}')
            if any(('REDIRECT ' in line or line.startswith('MISS ')) and 'title.pimg' in line and 'bgmtitle.pimg' not in line for line in logs.splitlines()):
                reached = True
                break
            if proc.poll() is not None:
                raise RuntimeError(f'Game exited before the title checkpoint: code={proc.returncode}; inspect {runtime_log}')
            time.sleep(0.25)
        if not pid or not _is_alive(pid):
            raise RuntimeError('Game did not remain running after runtime initialization')
        if not reached:
            raise RuntimeError(f'The title image was not read before timeout: {runtime_log}')
        success = True
        return {'pid':pid, 'alive':True, 'runtime_ready':True, 'title_resource_observed':reached, 'leave_running':leave_running, 'elapsed_seconds':round(time.monotonic() - started, 2)}
    finally:
        if pid and not (leave_running and success):
            stop_game(pid)
        if not (leave_running and success):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()


def build(args: argparse.Namespace) -> dict:
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
    out = ROOT / 'out'
    work = out / f'build_{run_id}'
    package = out / f'package_{run_id}'
    release = out / f'release_{run_id}'
    plan = cleanup_previous_outputs(project_root=ROOT, output_roots=[out], builder_id=BUILDER, current_run_id=run_id, exclude_paths=[work, package, release], keep_previous_outputs=args.keep_previous_outputs, dry_run=args.dry_run, log=lambda text: print(text, flush=True))
    if args.dry_run:
        preview = {'dry_run':True, 'remove':[str(p) for p in plan.remove], 'preserve':[{'path':str(p.path), 'reason':p.reason} for p in plan.preserve]}
        print(json.dumps(preview, ensure_ascii=True, indent=2))
        return preview
    tools = args.psb_tools.resolve()
    summary = {'run_id':run_id, 'cleanup_removed':[str(p) for p in plan.remove] if not args.keep_previous_outputs else [], 'package':str(package), 'release':str(release), 'build':str(work), 'smoke_sample':None}
    with ExitStack() as stack:
        stack.enter_context(generated_output(work, project_root=ROOT, output_roots=[out], builder_id=BUILDER, run_id=run_id, kind='build'))
        unpack = unpack_archive(ROOT / 'game/data.xp3', DEFAULT_OUTPUT, overwrite=True, verify_cache=args.verify_cache)
        summary['data_archive'] = {k:v for k,v in unpack.items() if k != 'files'}
        jsonroot = DEFAULT_OUTPUT / 'data/scn_json'
        summary['decompile'] = ensure_decompile(DEFAULT_OUTPUT / 'data/scn', jsonroot, tools)
        summary['export'] = export_text(jsonroot, ROOT / 'game_script/raw_script/scn', ROOT / 'game_script/translated_script/scn')
        translated = args.translation_dir.resolve()
        if args.smoke_sample:
            summary['smoke_sample'] = smoke_translations(translated, work / 'smoke_translations')
            translated = work / 'smoke_translations'
        summary['scripts'] = compile_translations(jsonroot, translated, work / 'scn_bin', tools, verify=True)
        patch = work / 'patch'
        patch.mkdir()
        summary['static_resources'] = static_resources(patch)
        summary['system_export'] = export_system()
        system_document = json.loads(args.system_translation.read_text('utf-8'))
        if args.smoke_sample:
            samples = {'default:renderSampleConfig.text1':'消息显示测试\n文本显示速度及自动播放等待时间示例\n１２３４５６７８９０１２３４５６７８９', 'default:renderSampleConfig.text2':'１２３４５６７８９０１２３４５６７８９\n消息显示测试\n文本显示速度及自动播放等待时间示例'}
            for row in system_document['records']:
                if row['id'] in samples:
                    row['translation'] = samples[row['id']]
            menu_samples = {'ファイル(&F)':'文件(&F)'}
            for bytecode in system_document['bytecode'].values():
                for row in bytecode['strings']:
                    if row['source'] in menu_samples:
                        row['enabled'] = True
                        row['translation'] = menu_samples[row['source']]
        system_outputs, system_changed = rebuild_system(system_document)
        selected_system = []
        for name, data in system_outputs.items():
            if data == (DEFAULT_OUTPUT / 'data' / name).read_bytes():
                continue
            destination = patch / name
            if destination.exists():
                raise ValueError(f'System text output conflicts with a static override: {name}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            selected_system.append(name)
        summary['system_text'] = {'changed_records':system_changed, 'override_files':selected_system}
        add_scn_overrides(summary['scripts']['binary_files'], work / 'scn_bin', patch)
        summary['runtime'] = build_runtime(work / 'runtime', verify=True)
        stack.enter_context(generated_output(package, project_root=ROOT, output_roots=[out], builder_id=BUILDER, run_id=run_id, kind='package'))
        (package / 'chs_patch').mkdir()
        summary['archive'] = pack_xp3(patch, package / 'chs_patch/patch.xp3', compress_index=False, compress_content=False)
        roundtrip = work / 'patch_roundtrip'
        actual = extract_archive(package / 'chs_patch/patch.xp3', roundtrip, verify_checksum=True)
        expected = {p.relative_to(patch).as_posix():digest(p) for p in patch.rglob('*') if p.is_file()}
        found = {item['name']:item['sha256'] for item in actual}
        if expected != found:
            raise RuntimeError('Final patch archive differs from its maintained inputs')
        summary['archive_roundtrip'] = {'files':len(found), 'hash_mismatches':0}
        shutil.copy2(summary['runtime']['dll']['path'], package / 'dc4_fd_chs.dll')
        shutil.copy2(summary['runtime']['launcher']['path'], package / 'DC4_FD_chs.exe')
        config = (ROOT / 'solution/runtime/config.ini').read_text('utf-8-sig')
        if args.run_smoke:
            config = config.replace('loglevel=1', 'loglevel=2')
        summary['runtime_assets'] = runtime_assets(package, config)
        (package / 'chs_patch/config.ini').write_bytes(b'\xff\xfe' + config.encode('utf-16-le'))
        for name in ('THIRD_PARTY.md', 'LICENSE.minhook.txt', 'LICENSE.krkrz.txt'):
            shutil.copy2(ROOT / 'solution/runtime' / name, package / name)
        if args.smoke_sample:
            mode = '本包包含用于技术验收的中文样例，尚非全篇汉化发布版。\n'
        elif summary['scripts']['changed_records'] == 0 and system_changed == 0 and not summary['static_resources']:
            mode = '本包为原文回填技术验证包，尚未加入正式译文。\n'
        else:
            mode = '本包使用当前正式译文与静态资源构建，完成度及发布范围以项目发布说明为准。\n'
        (package / 'readme.txt').write_text('D.C.4 - Fortunate Departures - 汉化技术补丁\n\n' + mode + '将本目录内容复制到同版本原版游戏目录，运行 DC4_FD_chs.exe。\n原版文件保持不变，汉化存档位于 savedata_chs/。\n', encoding='utf-8-sig')
        summary['package_manifest'] = verify_package_files(package)
        if not args.no_release:
            stack.enter_context(generated_output(release, project_root=ROOT, output_roots=[out], builder_id=BUILDER, run_id=run_id, kind='release', mutable_paths=MUTABLE_RELEASE))
            shutil.copytree(ROOT / 'game', release, dirs_exist_ok=True, ignore=shutil.ignore_patterns('savedata', 'savedata_chs', '.vn-build-output.json'))
            shutil.copytree(package, release, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.vn-build-output.json'))
            if args.run_smoke:
                summary['runtime_smoke'] = smoke_game(release, args.leave_running)
        write_json(work / 'pipeline_summary.json', summary)
    print(f'[done] package={package}', flush=True)
    if not args.no_release:
        print(f'[done] release={release}', flush=True)
    print(f'[done] summary={work / "pipeline_summary.json"}', flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Build and verify the DC4 FD localization package; clean previous owned outputs first.')
    parser.add_argument('--translation-dir', type=Path, default=ROOT / 'game_script/translated_script/scn')
    parser.add_argument('--psb-tools', type=Path, default=ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0')
    parser.add_argument('--system-translation', type=Path, default=ROOT / 'game_script/translated_script/system/system.json')
    parser.add_argument('--keep-previous-outputs', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify-cache', action='store_true')
    parser.add_argument('--smoke-sample', action='store_true')
    parser.add_argument('--run-smoke', action='store_true')
    parser.add_argument('--leave-running', action='store_true')
    parser.add_argument('--no-release', action='store_true')
    args = parser.parse_args()
    if args.leave_running and not args.run_smoke or args.run_smoke and args.no_release:
        parser.error('Use --leave-running with --run-smoke; smoke requires a release directory')
    build(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
