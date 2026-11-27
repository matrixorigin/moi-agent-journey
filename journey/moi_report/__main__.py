"""Explicit MOI Product API publication. Requires caller-provided authentication."""
import argparse
import json
import os
import re
from pathlib import Path

from journey.client import Client
from .publish import Publication
from .tables import write_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'publish'))
    parser.add_argument('--input', type=Path, help='Reviewed dashboard-tables.json for publish')
    parser.add_argument('--comparison', type=Path, help='comparison.json for prepare')
    parser.add_argument('--output', type=Path, required=True, help='New prepare directory or private publish ledger')
    parser.add_argument('--workspace-id', help='Required for publish; never inferred')
    parser.add_argument('--database-name', help='New unique database for a new publication')
    parser.add_argument('--title', help='Dashboard title')
    parser.add_argument('--description', default='J2 report: retain source scores, coverage, provenance and comparison limits.')
    parser.add_argument('--resume', action='store_true', help='Continue only this exact saved scope; no new publication')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        if not args.comparison or args.workspace_id or args.database_name or args.title or args.resume:
            parser.error('prepare only accepts --comparison and --output')
        print(json.dumps({'tables': str(write_inputs(args.comparison, args.output)), 'production_writes': 0}))
        return 0
    if (not args.input or not args.workspace_id or not re.fullmatch(
            r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', args.workspace_id)):
        parser.error('publish requires --input and a valid explicit --workspace-id')
    if args.resume and (args.database_name or args.title):
        parser.error('--resume uses only the identity recorded in the existing ledger')
    if not args.resume and (not args.database_name or not args.title):
        parser.error('new publication requires --database-name and --title')
    if args.resume and not args.output.is_file():
        parser.error('--resume requires an existing private publication ledger')
    client = Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=900,
                    locked_workspace=args.workspace_id)
    data = client.product('GET', '/workspaces', expected_code='OK')
    matches = [item for item in data['workspaces'] if item.get('id') == args.workspace_id]
    if len(matches) != 1 or matches[0].get('status') != 'ACTIVE':
        raise ValueError('selected_workspace_not_active')
    publication = Publication(client, json.loads(args.input.read_text()),
                              database_name=args.database_name or 'resume_from_ledger',
                              title=args.title or 'Resume saved publication', description=args.description,
                              state_path=args.output)
    state = publication.publish()
    print(json.dumps({'stage': state['stage'], 'dashboard_id': state['dashboard_id'],
                      'database_id': state['database_id'],
                      'tables': {k: v.get('rows', 0) for k, v in state['tables'].items()},
                      'charts': len(state['charts'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
