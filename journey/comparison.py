"""Publish historical Codex evidence alongside current QA; never mix denominators."""
import csv
import hashlib
import html
import json
import shutil
from collections import Counter
from pathlib import Path

LABELS = {'passed':'通过','partial':'部分通过','failed':'未通过','blocked':'阻塞',
          'missing':'结果缺失/未运行','error':'框架或验收错误','unavailable':'未完成验收',
          'partial_validation':'仅部分检查'}


def current_state(row):
    reasons = {x.get('reason') for x in row.get('harness_errors',[])}
    if reasons & {'case_artifact_missing','preparation_did_not_finish','execution_did_not_start'}:
        return 'missing'
    if row.get('test_status') == 'error':
        return 'error'
    quality = row.get('quality',{})
    if quality.get('state') == 'checked':
        return 'failed' if quality.get('failed_checks') else 'passed'
    if quality.get('state') == 'partial':
        return 'partial_validation'
    return 'unavailable'


def load_baseline(root):
    directory = Path(root)/'baselines/journey-4'
    baseline = json.loads((directory/'codex-2026-10-29.json').read_text())
    cases = baseline['cases']
    expected = {f'J4-{n:02d}' for n in range(1,101)}
    if len(cases) != 100 or {c['case_id'] for c in cases} != expected:
        raise ValueError('baseline_case_mapping_incomplete')
    if dict(Counter(c['verdict'] for c in cases)) != baseline['counts']:
        raise ValueError('baseline_counts_mismatch')
    for case in cases:
        for file in case['evidence']:
            path = directory/file['path']
            if (not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink() or
                    hashlib.sha256(path.read_bytes()).hexdigest() != file['public_sha256']):
                raise ValueError('baseline_evidence_checksum_mismatch')
    return baseline, directory


def build(rows, root):
    selected = [r for r in rows if r['workspace'] == 'journey-4']
    if not selected:
        return None
    baseline, _ = load_baseline(root)
    by_id = {c['case_id']:c for c in baseline['cases']}
    items = []
    for row in selected:
        historic = by_id[row['case_id']]
        items.append({'case_id':row['case_id'],'original_id':historic['original_id'],'title':row['title'],
            'current_state':current_state(row),'current_task_seconds':row.get('task_seconds'),
            'current_test_status':row['test_status'],'current_quality':row['quality'],
            'current_failures':row.get('failures',[]),'current_harness_errors':row.get('harness_errors',[]),
            'codex_verdict':historic['verdict'],'codex_review':historic['review'],
            'historical_moi_verdict':historic['historical_moi_verdict'],
            'historical_moi_review':historic['historical_moi_review'],
            'codex_observed_start_at':historic['observed_start_at'],
            'codex_observed_finished_at':historic['observed_finished_at'],
            'timing_comparable':False,'speed_ratio':None,
            'codex_result':'baseline/codex/'+historic['original_id']+'/result.md',
            'codex_execution':'baseline/codex/'+historic['original_id']+'/execution.md',
            'current_result':'cases/'+row['case_id']+'/case-summary.md'})
    return {'baseline_id':baseline['id'],'baseline_date':baseline['date'],'baseline_model':baseline['model'],
        'baseline_environment':baseline['environment'],'baseline_method':baseline['method'],
        'comparison_limits':baseline['comparison_limits'],'current_counts':dict(Counter(r['current_state'] for r in items)),
        'historical_codex_counts':dict(Counter(r['codex_verdict'] for r in items)),
        'historical_counts_in_current_denominator':False,'speed_comparable':False,'cases':items}


def markdown(comparison):
    def cell(value):
        return str(value).replace('|','／').replace('\n',' ')
    lines = ['', '## Journey-4 与历史 Codex 对照', '',
        f"历史 Codex：{comparison['baseline_date']} / {comparison['baseline_model']}，"
        f"{comparison['historical_codex_counts']}。本次 QA：{comparison['current_counts']}。", '',
        '历史评分不计入本次通过数；本次为自动检查，历史为人工成果验收，不能直接计算产品胜率或速度倍数。', '',
        '| Case | 业务任务 | 本次 MOI QA 检查 | 历史 MOI 人工验收 | 历史 Codex 人工验收 | Codex 依据 |',
        '|---|---|---|---|---|---|']
    for r in comparison['cases']:
        lines.append(f"| {r['case_id']} / {r['original_id']} | {cell(r['title'])} | "
            f"{LABELS[r['current_state']]} | {cell(r['historical_moi_verdict'])} | "
            f"{LABELS[r['codex_verdict']]} | {cell(r['codex_review'])} |")
    lines += ['', '[逐项报告和 Codex 原交付正文](comparison.html)；JSON/CSV：comparison.json、comparison.csv。', '']
    lines += ['- '+s for s in comparison['comparison_limits']]
    return '\n'.join(lines)+'\n'


def publish(comparison, destination, root):
    if comparison is None:
        return
    destination = Path(destination)
    _, source = load_baseline(root)
    shutil.copytree(source,destination/'baseline',dirs_exist_ok=True)
    (destination/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
    fields = ['case_id','original_id','title','current_state','historical_moi_verdict','historical_moi_review',
              'codex_verdict','codex_review','current_task_seconds','codex_observed_start_at','codex_observed_finished_at',
              'timing_comparable','speed_ratio','codex_result','codex_execution']
    with (destination/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader();writer.writerows(comparison['cases'])
    (destination/'comparison.md').write_text(markdown(comparison))
    esc = lambda v: html.escape(str(v),quote=True)
    body = []
    for r in comparison['cases']:
        current = (f'<a href="{esc(r["current_result"])}">{esc(LABELS[r["current_state"]])}</a>'
                   if r['current_state'] != 'missing' else esc(LABELS['missing']))
        body.append('<tr><td>'+esc(r['case_id']+' / '+r['original_id'])+'</td><td>'+esc(r['title'])+
            '</td><td>'+current+'</td><td>'+esc(r['historical_moi_verdict'])+'</td><td>'+esc(LABELS[r['codex_verdict']])+
            '</td><td>'+esc(r['codex_review'])+'<p><a href="'+esc(r['codex_result'])+'">交付正文</a> · <a href="'+
            esc(r['codex_execution'])+'">执行记录</a></p></td><td>'+esc(r.get('current_task_seconds'))+'</td></tr>')
    doc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Journey-4 与 Codex 历史对照</title>'
    doc += '<style>body{font:15px system-ui;margin:32px;color:#192c36}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ccc;text-align:left;vertical-align:top}th{background:#edf3f6}p,li{line-height:1.6}a{color:#075ba5}</style>'
    doc += '<h1>Journey-4 与 Codex 历史对照</h1><p>'+esc(comparison['baseline_date']+' / '+comparison['baseline_model'])+'</p>'
    doc += '<p>历史 Codex：'+esc(comparison['historical_codex_counts'])+'；本次 QA：'+esc(comparison['current_counts'])+'</p>'
    doc += '<p>本次 QA 检查与历史人工验收分别呈现。历史成绩不计入本次通过数；新用例未运行时保持缺失，不生成假通过。</p><ul>'
    doc += ''.join('<li>'+esc(s)+'</li>' for s in comparison['comparison_limits'])+'</ul>'
    doc += '<table><thead><tr><th>Case</th><th>业务任务</th><th>本次 MOI QA</th><th>历史 MOI</th><th>历史 Codex</th><th>Codex 依据与成果</th><th>本次耗时/秒</th></tr></thead><tbody>'
    (destination/'comparison.html').write_text(doc+''.join(body)+'</tbody></table></html>')
