"""Run validators in a constrained container; never execute agent code on the runner."""
import argparse
import json
import subprocess
import time
from pathlib import Path, PurePosixPath

from .catalog import ROOT, load_case
from .evidence import Evidence, configured_secrets


def lineage_checks(lineage, previous_sessions, session_id):
    valid = isinstance(lineage, dict) and isinstance(lineage.get('sources'), list) and all(
        isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in ('session_id', 'path', 'sha256'))
        for entry in lineage['sources'])
    if not valid:
        return [{'session': session_id, 'rule': '跨会话溯源 JSON 结构', 'passed': False,
                 'detail': 'sources 必须是包含 session_id、path、sha256 字符串字段的对象列表'}]
    expected = {(session['id'], name, digest) for session in previous_sessions
                for name, digest in session['outputs'].items()}
    entries = []
    for source in lineage['sources']:
        name = source['path']
        relative = PurePosixPath(name)
        if not relative.is_absolute() and '..' not in relative.parts:
            name = relative.as_posix()
        prefix = 'prior_outputs/' + source['session_id'] + '/'
        if name.startswith(prefix):
            name = name[len(prefix):]
        entries.append((source['session_id'], name, source['sha256']))
    actual = set(entries)
    valid_entries = len(entries) == len(actual) and actual == expected
    return [{'session': session_id, 'rule': '跨会话前序真实产物的文件名和 SHA256', 'passed': valid_entries,
             'detail': json.dumps({'expected': sorted(expected), 'actual': sorted(actual),
                                   'duplicate_entries': len(entries) - len(actual)})}]


def validate(case, work, evidence):
    result = json.loads((evidence.root / "result.json").read_text())
    if result["status"] != "awaiting_validation":
        return result
    started = time.monotonic()
    work = Path(work).resolve()
    (work / "data").mkdir(exist_ok=True)
    link = work / "data/input_files"
    if not link.exists():
        link.symlink_to("../inputs", target_is_directory=True)
    checks = []
    def checkpoint_checks():
        result["validation"] = {"checks": list(checks),
            "failed_checks": [item for item in checks if not item["passed"]],
            "seconds": round(time.monotonic()-started,3)}
        evidence.write("result.json", result)

    for session in result["sessions"]:
        source = evidence.root / "sessions" / session["id"] / "outputs"
        destination = evidence.root / "sessions" / session["id"] / "verification"
        destination.mkdir(parents=True, exist_ok=True)
        # The validator user can write evidence, but cannot alter the submitted files.
        destination.chmod(0o777)
        command = ["docker", "run", "--rm", "--init", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--memory=3g", "--cpus=2", "--pids-limit=256",
            "--tmpfs", "/tmp:rw,size=1g,mode=1777", "--mount", f"type=bind,src={work},dst=/work,readonly",
            "--mount", f"type=bind,src={source.resolve()},dst=/outputs,readonly",
            "--mount", f"type=bind,src={destination.resolve()},dst=/verification",
            "--mount", f"type=bind,src={ROOT / 'cases' / case['workspace'] / case['id'] / 'case.json'},dst=/case.json,readonly",
            "moi-journey-judge", "--case", "/case.json", "--session", session["id"]]
        process = subprocess.run(command, capture_output=True, text=True, timeout=600)
        (destination / "judge.log").write_text(evidence.text(process.stdout + process.stderr))
        path = destination / "verdict.json"
        if not path.exists():
            raise RuntimeError("validator_process_error:" + session["id"] + ":exit=" + str(process.returncode))
        verdict = json.loads(path.read_text())
        if verdict["status"] != "completed":
            raise RuntimeError("validator_error:" + str(verdict.get("reason")))
        if process.returncode:
            raise RuntimeError("validator_process_error:" + session["id"] + ":exit=" + str(process.returncode))
        checks += [{"session": session["id"], **item} for item in verdict["checks"]]
        checkpoint_checks()
    if case['source']['benchmark'] == 'Business-100':
        from .business import saved_checks
        verdicts = json.loads((evidence.root/'semantic-verdicts.json').read_text())
        checks += saved_checks(case, verdicts, evidence.root)
    if case["source"]["benchmark"] == "π-Bench":
        verdicts = json.loads((evidence.root / "semantic-verdicts.json").read_text())
        checks += [{"session": v["session"], "rule": item["id"] + ":" + v["criteria"][item["id"]],
                    "passed": item["passed"], "detail": item["reason"]} for v in verdicts for item in v["checks"]]
    if case['source']['benchmark'] == 'π-Bench':
        final = result["sessions"][-1]
        lineage_path = evidence.root / "sessions" / final["id"] / "outputs/source_lineage.json"
        try:
            lineage = json.loads(lineage_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            lineage = None
        checks += lineage_checks(lineage, result['sessions'][:-1], final['id'])
    failed = [item for item in checks if not item["passed"]]
    checkpoint_checks()
    result["status"] = "failed" if failed else "passed"
    if failed:
        result["failures"].append({"stage": "validation", "reason": "acceptance_checks_failed", "checks": failed})
    result["stage"] = "complete"
    evidence.write("result.json", result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--work",default="work")
    args=parser.parse_args()
    case=load_case(args.case_id)
    evidence=Evidence(Path('results')/case['id'], configured_secrets())
    try:
        result=validate(case,args.work,evidence)
    except Exception as error:
        result=json.loads((evidence.root/'result.json').read_text())
        result['status']='error'
        result['harness_errors'].append({'stage':'validation','reason':type(error).__name__+':'+str(error)})
        evidence.write('result.json',result)
    print(json.dumps({'case_id':case['id'],'status':result['status'],'harness_errors':len(result['harness_errors'])}))
    return 1 if result['harness_errors'] else 0


if __name__=='__main__':
    raise SystemExit(main())
