"""Account for every configured case, including missing artifacts and killed runners."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from .catalog import ROOT, load_case
from .diagnostics import observed_tool_errors
from .evidence import Evidence, utc_now,TOKEN_BYTES

FINAL = {"passed", "failed", "timeout", "error"}


def cell(value, limit=1000):
    value = str(value if value is not None else '—').replace('|', '／').replace('\n', ' ').replace('\r', ' ')
    return value if len(value) <= limit else value[:limit] + '…（完整内容见 result.json）'


def case_summary(result):
    status = result['status']
    labels = {'passed': '通过', 'failed': '未通过', 'timeout': '超时', 'error': '未得出判定'}
    verdict = labels[status]
    if result['harness_status'] == 'error':
        verdict = '无法确认：执行、验收或取证出错'
    lines = [f"# {cell(result['case_id'])} {cell(result['title'])}", '',
             f"**结果：{verdict}**", '',
             f"- 任务结果：`{status}`；执行/取证状态：`{result['harness_status']}`。",
             f"- 工作区：{cell(result['workspace'])}。",
             f"- 任务耗时：{cell(result.get('task_seconds'))} 秒；准备、QA 和验收合计：{cell(result.get('total_seconds'))} 秒。"]
    checks = result.get('validation', {}).get('checks', [])
    if checks:
        lines.append(f"- 验收项：通过 {sum(bool(item.get('passed')) for item in checks)}/{len(checks)}。")
    failures = result.get('failures', [])
    errors = result.get('harness_errors', [])
    if failures or errors:
        lines += ['', '## 不通过或错误原因', '']
        for kind, items in [('任务', failures), ('执行/验收/取证', errors)]:
            for item in items:
                detail = item.get('details')
                if isinstance(detail, (dict, list)):
                    detail = json.dumps(detail, ensure_ascii=False)
                suffix = f"；详情：{cell(detail)}" if detail else ''
                lines.append(f"- {kind} / {cell(item.get('stage'))}：{cell(item.get('reason'))}{suffix}")
    checks = result.get('validation', {}).get('failed_checks', [])
    if checks:
        lines += ['', f"## 未通过的验收项（{len(checks)} 项）", '']
        for check in checks[:20]:
            lines.append(f"- {cell(check.get('session'))} / {cell(check.get('rule'))}：{cell(check.get('detail'))}")
        if len(checks) > 20:
            lines.append(f"- 其余 {len(checks) - 20} 项见 result.json。")
    tasks = result.get('tasks', [])
    if tasks:
        lines += ['', '## 任务与 trace', '', '| 阶段 | Task ID | Trace ID | 事件数 |', '|---|---|---|---:|']
        for task in tasks:
            lines.append(f"| {cell(task.get('phase'))} | {cell(task.get('task_id'))} | {cell(task.get('trace_id'))} | {cell(task.get('event_count'))} |")
        lines += ['', f"事件与 trace 采集完整：{'是' if result.get('capture_complete') else '否'}。"]
    observed = result.get('observed_tool_errors', [])
    if observed:
        lines += ['', f"运行中观察到 {len(observed)} 条工具错误；这不一定是最终失败原因，详见 result.json 和 events.jsonl。"]
    lines += ['', '完整判词、逐轮事件与产物见本 case artifact 中的 result.json、events.jsonl 和其他证据文件；'
              '公开包中被隔离的文件见 publication.json。', '',
              '通过与否反映当前验收判据，需结合判词和证据复核。', '']
    return '\n'.join(lines)


def finish_case(case_id, directory):
    case = load_case(case_id)
    evidence = Evidence(Path(directory) / case_id, [os.environ.get("MOI_PAT", ""), os.environ.get("MOI_BASE_URL", "")])
    path = evidence.root / "result.json"
    if path.exists():
        result = json.loads(path.read_text())
    else:
        result = {"case_id": case_id, "workspace": case["workspace"], "title": case["title"],
                  "status": "error", "tasks": [], "failures": [], "harness_errors": []}
        prep = evidence.root / "preparation.json"
        reason = json.loads(prep.read_text()).get("reason", "execution_did_not_start") if prep.exists() else "preparation_did_not_finish"
        result["harness_errors"].append({"stage": "setup", "reason": reason})
    result.setdefault('case_id', case_id)
    result.setdefault('workspace', case['workspace'])
    result.setdefault('title', case['title'])
    if result["status"] not in FINAL:
        result["harness_errors"].append({"stage": result.get("stage"), "reason": "case_did_not_reach_final_verdict"})
        result["status"] = "error"
    if (result.get("tasks") or result["status"] == "passed") and not result.get("capture_complete"):
        result["harness_errors"].append({"stage": "evidence", "reason": "incomplete_trace_or_events"})
    events = evidence.root / 'events.jsonl'
    if events.exists():
        try:
            result['observed_tool_errors'] = observed_tool_errors(events)
        except (OSError, ValueError, UnicodeError) as error:
            result['harness_errors'].append({'stage': 'evidence', 'reason': 'invalid_event_log:' + str(error)})
    elif any(task.get('event_count', 0) for task in result.get('tasks', [])):
        result['harness_errors'].append({'stage': 'evidence', 'reason': 'recorded_events_missing'})
    result["harness_status"] = "error" if result["harness_errors"] else "ok"
    preparation=evidence.root/'preparation.json'
    if preparation.exists():
        result['preparation_seconds']=json.loads(preparation.read_text()).get('seconds')
    durations=[value for value in [result.get('preparation_seconds'),result.get('elapsed_seconds'),
        result.get('validation',{}).get('seconds')] if isinstance(value,(int,float))]
    result['total_seconds']=round(sum(durations),3) if durations else None
    result['github_run_id']=os.environ.get('GITHUB_RUN_ID')
    result['github_run_attempt']=os.environ.get('GITHUB_RUN_ATTEMPT')
    result['workflow_commit']=os.environ.get('GITHUB_SHA')
    specification=json.dumps(case,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    result['case_spec_sha256']=hashlib.sha256(specification).hexdigest()
    result["finalized_at"] = utc_now()
    result = evidence.clean(result)
    evidence.write("result.json", result)
    (evidence.root / 'case-summary.md').write_text(evidence.text(case_summary(result)), encoding='utf-8')
    return result


class ArtifactRejected(ValueError):
    pass


def inspect_payload(data, secrets, depth=0,name=''):
    """A failure is an explicit withholding decision, never permission to publish."""
    if any(s.encode() in data for s in secrets if s) or TOKEN_BYTES.search(data):
        raise ArtifactRejected("credential_detected_in_artifact")
    if re.search(r'\.(?:z\d{2,}|zip\.(?:part)?\d+)$',name,re.I):
        raise ArtifactRejected('split_archive_volume_not_inspectable')
    container=Path(name).suffix.lower() in {'.zip','.docx','.xlsx','.pptx','.xlsm','.docm','.pptm'}
    if container or data.startswith(b'PK\x03\x04') or zipfile.is_zipfile(io.BytesIO(data)):
        if depth>=5:raise ArtifactRejected("artifact_nested_archive_limit")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(f.file_size for f in archive.infolist())>1_000_000_000:
                    raise ArtifactRejected("artifact_archive_expansion_limit")
                for member in archive.infolist():
                    inspect_payload(member.filename.encode(),secrets,depth+1)
                    if not member.is_dir():inspect_payload(archive.read(member),secrets,depth+1,member.filename)
        except ArtifactRejected:
            raise
        except (zipfile.BadZipFile,ValueError,RuntimeError,NotImplementedError) as error:
            raise ArtifactRejected("archive_not_inspectable:"+type(error).__name__+":"+str(error)) from error


def scan(directory, secrets):
    """Strict diagnostic scan, also used by offline tests."""
    for file in Path(directory).rglob("*"):
        if file.is_symlink():
            raise ArtifactRejected("artifact_symlink_not_allowed")
        if file.is_file():
            inspect_payload(file.read_bytes(),secrets,name=file.name)


def aggregate(source, destination):
    config = json.loads((ROOT / "suites.json").read_text())
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    rows = []
    suites = []
    for suite in config["journeys"]:
        current = []
        for case_id in suite["cases"]:
            case = load_case(case_id)
            file = Path(source) / case_id / "result.json"
            if file.exists():
                result = json.loads(file.read_text())
                if result.get("case_id") != case_id or result.get("workspace") != suite["workspace"]:
                    raise ValueError("result_identity_mismatch")
            else:
                result = {"case_id": case_id, "workspace": suite["workspace"], "title": case["title"],
                    "status": "error", "harness_status": "error", "tasks": [], "failures": [],
                    "harness_errors": [{"stage": "aggregation", "reason": "case_artifact_missing"}]}
            if result.get("harness_status") != "ok" or result.get("status") not in FINAL:
                result["status"] = "error"
            rows.append(result)
            current.append(result)
        passed = sum(r["status"] == "passed" for r in current)
        suites.append({"workspace": suite["workspace"], "concurrency": suite["concurrency"],
            "status": "empty" if not current else "configured", "total": len(current), "passed": passed,
            "success_rate": passed / len(current) if current else None})
    total = len(rows)
    passed = sum(r["status"] == "passed" for r in rows)
    errors = sum(r["status"] == "error" for r in rows)
    report = {"generated_at": utc_now(), "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"), "commit": os.environ.get("GITHUB_SHA"),
        "total": total, "passed": passed, "failed": sum(r["status"] == "failed" for r in rows),
        "timeout": sum(r["status"] == "timeout" for r in rows), "harness_errors": errors,
        "success_rate": passed / total if total else None, "journeys": suites, "cases": rows,
        "withheld_files":sum(len(r.get('publication',{}).get('withheld_files',[])) for r in rows)}
    (destination / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    with (destination / "summary.csv").open("w", newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["workspace","case_id","title","status","harness_status","task_seconds","semantic_judge_seconds","preparation_seconds","validation_seconds","cleanup_seconds","total_seconds","trace_ids","failure_reasons","observed_tool_errors"])
        for row in rows:
            reasons = row.get("failures",[]) + row.get("harness_errors",[])
            writer.writerow([row["workspace"],row["case_id"],row["title"],row["status"],row.get("harness_status","error"),
                row.get("task_seconds"),row.get('semantic_judge_seconds'),row.get('preparation_seconds'),row.get("validation",{}).get("seconds"),row.get('cleanup_seconds'),row.get("total_seconds"),
                ";".join(t.get("trace_id","") for t in row.get("tasks",[])),json.dumps(reasons,ensure_ascii=False),
                json.dumps(row.get('observed_tool_errors',[]),ensure_ascii=False)])
    lines=["# MOI agent journeys", "",f"**通过 {passed}/{total}（{passed/total:.1%}）**" if total else "未配置任务", "",
           f"任务不通过：{report['failed']}；超时：{report['timeout']}；执行/证据错误：{errors}；隔离产物：{report['withheld_files']}。", "",
           "空工作区不计入分母；任务不通过、超时或执行/验收/取证错误会使对应 job 和工作流失败，但不会阻止其他任务及结果上传。", "",
           "通过率反映当前检查器的判定；部分上游判据尚待校准，请结合判词和原始证据复核。", "",
           "| 工作区 | 用例 | 结果 | 任务耗时（秒） | 不通过/错误原因 |", "|---|---|---|---:|---|"]
    for row in rows:
        reasons=[r.get('reason','') for r in row.get('failures',[])+row.get('harness_errors',[])]
        if row.get('validation',{}).get('failed_checks'):
            failed_checks=row['validation']['failed_checks']
            reasons += [r['rule'] for r in failed_checks[:5]]
            if len(failed_checks)>5:reasons.append(f"共 {len(failed_checks)} 项，完整明细见 result.json")
        if row['status'] != 'passed':
            observed = row.get('observed_tool_errors', [])
            reasons.extend('观察到工具错误: ' + str(item.get('code') or item.get('error_kind') or 'failed')
                           for item in observed[:3])
            if len(observed) > 3:reasons.append(f"共观察到 {len(observed)} 条，完整明细见 result.json")
        lines.append(f"| {row['workspace']} | {row['case_id']} {cell(row['title'])} | {row['status']} | {row.get('task_seconds','—')} | {cell('; '.join(reasons))} |")
    for suite in suites:
        if suite['status']=='empty':lines.append(f"| {suite['workspace']} | 待配置 | empty | — | 不计入成功率 |")
    lines += ["", "每个 case 的 result.json 包含逐轮 task ID、trace ID、事件数量与种类、验收失败明细；events.jsonl 保存公开事件原始字段（凭据已脱敏）。", "",
        "任务预算为每 case 3600 秒，包含全部对话及 Pi-Bench 语义评分。准备、确定性验收和最终取证另计。", "",
        "下载本次运行的 journey-run-* artifact，即可得到 summary.json、summary.csv 和全部 case 证据。Artifact 保留 1 天。"]
    markdown="\n".join(lines)+"\n"
    (destination/'summary.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as output:output.write(markdown)
    # Keep relative per-case paths intact in the consolidated download.
    if Path(source).exists():
        shutil.copytree(source,destination/'cases',dirs_exist_ok=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    finish=sub.add_parser('case');finish.add_argument('case_id');finish.add_argument('--root',default='results')
    security=sub.add_parser('scan');security.add_argument('directory')
    summary=sub.add_parser('aggregate');summary.add_argument('--root',default='downloaded-results');summary.add_argument('--out',default='bundle')
    args=parser.parse_args()
    if args.command=='case':
        result=finish_case(args.case_id,args.root)
        markdown=(Path(args.root)/args.case_id/'case-summary.md').read_text(encoding='utf-8')
        print(markdown)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write(markdown)
        return 0 if result['status']=='passed' and result['harness_status']=='ok' else 1
    if args.command=='scan':
        scan(args.directory,[os.environ.get('MOI_PAT',''),os.environ.get('MOI_BASE_URL','')])
        return 0
    report=aggregate(args.root,args.out)
    print(json.dumps({k:report[k] for k in ['total','passed','failed','timeout','harness_errors','success_rate']}))
    return 1 if report['failed'] or report['timeout'] or report['harness_errors'] else 0


if __name__=='__main__':
    raise SystemExit(main())
