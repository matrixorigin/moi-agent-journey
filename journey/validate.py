"""Run validators in a constrained container; never execute agent code on the runner."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from .catalog import ROOT, load_case
from .evidence import Evidence


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
    for session in result["sessions"]:
        source = evidence.root / "sessions" / session["id"] / "outputs"
        destination = evidence.root / "sessions" / session["id"] / "verification"
        destination.mkdir(parents=True, exist_ok=True)
        # The validator user can write evidence, but cannot alter the submitted files.
        destination.chmod(0o777)
        command = ["docker", "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
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
    if case["source"]["benchmark"] == "π-Bench":
        verdicts = json.loads((evidence.root / "semantic-verdicts.json").read_text())
        checks += [{"session": v["session"], "rule": item["id"] + ":" + v["criteria"][item["id"]],
                    "passed": item["passed"], "detail": item["reason"]} for v in verdicts for item in v["checks"]]
        final = result["sessions"][-1]
        lineage_path = evidence.root / "sessions" / final["id"] / "outputs/source_lineage.json"
        lineage = json.loads(lineage_path.read_text())
        valid = isinstance(lineage, dict) and isinstance(lineage.get("sources"), list) and all(
            isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in ["session_id","path","sha256"])
            for entry in lineage["sources"])
        expected = {(s["id"], name, digest) for s in result["sessions"][:-1] for name, digest in s["outputs"].items()}
        if not valid:
            checks.append({"session": final["id"], "rule": "跨会话溯源 JSON 结构", "passed": False,
                           "detail": "sources 必须是包含 session_id、path、sha256 字符串字段的对象列表"})
            actual = None
        else:
            actual = {(entry["session_id"], entry["path"], entry["sha256"]) for entry in lineage["sources"]}
        checks.append({"session": final["id"], "rule": "跨会话前序真实产物的文件名和 SHA256", "passed": actual == expected,
                       "detail": json.dumps({"expected": sorted(expected), "actual": sorted(actual) if valid else None})})
    failed = [item for item in checks if not item["passed"]]
    result["validation"] = {"checks": checks, "failed_checks": failed, "seconds": round(time.monotonic()-started,3)}
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
    evidence=Evidence(Path('results')/case['id'],[os.environ.get('MOI_PAT',''),os.environ.get('MOI_BASE_URL','')])
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
