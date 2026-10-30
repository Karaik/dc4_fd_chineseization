from __future__ import annotations

import argparse
import json
from pathlib import Path

from solution.script.scn import compile_translations, ensure_decompile, export_text


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Export and recompile DC4 FD SCN text with complete JSON verification')
    parser.add_argument('--tool', type=Path, default=ROOT / 'tools/Ulysses-FreeMoteToolkit-v4.3.0')
    commands = parser.add_subparsers(dest='command', required=True)
    decompile = commands.add_parser('decompile', help='Create FreeMote JSON and readable instruction files')
    decompile.add_argument('source', type=Path)
    decompile.add_argument('jsonroot', type=Path)
    decompile.add_argument('--force', action='store_true')
    export = commands.add_parser('export', help='Create guarded bilingual text without overwriting translations')
    export.add_argument('jsonroot', type=Path)
    export.add_argument('--raw', type=Path, default=ROOT / 'game_script/raw_script/scn')
    export.add_argument('--translated', type=Path, default=ROOT / 'game_script/translated_script/scn')
    build = commands.add_parser('compile', help='Apply all translations and verify every rebuilt JSON tree')
    build.add_argument('jsonroot', type=Path)
    build.add_argument('outbin', type=Path)
    build.add_argument('--translated', type=Path, default=ROOT / 'game_script/translated_script/scn')
    args = parser.parse_args(argv)
    if args.command == 'decompile':
        result = ensure_decompile(args.source, args.jsonroot, args.tool, force=args.force)
    elif args.command == 'export':
        result = export_text(args.jsonroot, args.raw, args.translated)
    else:
        result = compile_translations(args.jsonroot, args.translated, args.outbin, args.tool, verify=True)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
