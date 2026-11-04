"""Command line for verified localization handoff and partial returns."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from solution.patch.handoff_catalog import write_catalog
from solution.patch.handoff_return import prepare_return

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Bind a delivered asset ZIP and import reviewed partial returns into an isolated build candidate.')
    commands = parser.add_subparsers(dest='command', required=True)
    catalog = commands.add_parser('catalog', help='Verify the actual delivered ZIP against original resources and retain its local mapping')
    catalog.add_argument('--package', type=Path, required=True)
    catalog.add_argument('--password', required=True)
    catalog.add_argument('--output', type=Path, default=ROOT / 'solution/patch/handoff_catalog.json')
    returned = commands.add_parser('import', help='Import changed files; preserve all formal translations and static resources')
    returned.add_argument('--input', type=Path, required=True)
    returned.add_argument('--catalog', type=Path, default=ROOT / 'solution/patch/handoff_catalog.json')
    returned.add_argument('--password', help='Password only when the returned ZIP is encrypted')
    returned.add_argument('--output', type=Path)
    returned.add_argument('--reviewed-system-edits', action='store_true', help='Enable changed system documents/constants after program review')
    returned.add_argument('--replace-existing-static', action='store_true', help='Replace a conflicting maintained static resource in this candidate only')
    args = parser.parse_args(argv)
    try:
        if args.command == 'catalog':
            result = write_catalog(args.package, args.password.encode('utf-8'), args.output)
            summary = {'project': result['project'], 'catalog': str(args.output), 'counts': result['counts']}
        else:
            result = prepare_return(args.input, args.catalog, args.output,
                                    password=args.password.encode('utf-8') if args.password else None,
                                    reviewed_system=args.reviewed_system_edits,
                                    replace_static=args.replace_existing_static)
            summary = {'project': result['project'], 'output': result['output'],
                       'received_files': result['received_files'], 'changed_groups': len(result['changed']),
                       'static_resources': len(result['static_files']), 'reviewed_system_fields': result['reviewed_system_fields'],
                       'build_command': 'python .\\main_build.py --handoff "' + str(Path(result['output']).relative_to(ROOT)) + '"'}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print('Handoff failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
