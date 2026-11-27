"""Explicit offline CLI; never submits tasks, calls a judge, or writes to MOI."""
import argparse
import csv
import json
from pathlib import Path

from .compare import build_comparison, markdown
from .html import render_html
from .release_baseline import select_previous_release
from .runs import DEFAULT_SUITE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('score', 'compare'))
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--current-run', type=Path, required=True)
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument('--baseline-run', type=Path)
    baseline.add_argument('--history-root', type=Path,
                          help='Select the latest completed test of a different product release')
    parser.add_argument('--allow-suite-projection', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == 'score' and (args.baseline_run or args.history_root):
        parser.error('score does not accept a baseline; use compare')
    if args.allow_suite_projection and args.action != 'compare':
        parser.error('suite projection is only available for explicit comparisons')
    selection = select_previous_release(args.current_run, args.history_root) if args.history_root else None
    previous = selection['selected_run'] if selection else args.baseline_run
    report = build_comparison(args.suite, previous, args.current_run,
                              allow_suite_projection=args.allow_suite_projection)
    if selection:
        report['baseline_selection'] = selection
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'report.md').write_text(markdown(report))
    (args.output / 'report.html').write_text(render_html(report))
    with (args.output / 'results.csv').open('w', newline='') as output:
        fields = ('case_id', 'domain', 'score', 'verdict', 'excluded_reason', 'actual_checks',
                  'expected_checks', 'evidence_path', 'result_sha256')
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in report['cases']:
            writer.writerow({key: row['current'].get(key) for key in fields})
    print(json.dumps({'output': str(args.output.resolve()), 'cases': len(report['cases']),
                      'scored': report['current']['scored'], 'coverage': report['current']['coverage']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
