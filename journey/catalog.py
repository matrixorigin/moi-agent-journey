"""Versioned case catalog and checksum-verified, credential-free preparation."""
import argparse
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .evidence import Evidence, sha256, utc_now

ROOT = Path(__file__).resolve().parent.parent


def load_suite(name):
    config = json.loads((ROOT / "suites.json").read_text())
    return next(s for s in config["journeys"] if s["workspace"] == name)


def case_executor(case):
    if 'executor' in case:
        return case['executor']
    return load_suite(case['workspace']).get('executor')


def is_functional_case(case):
    return case_executor(case) in {'api', 'browser'}


def load_case(case_id):
    if not re.fullmatch(r"J[1-5]-[0-9]{2}", case_id):
        raise ValueError("invalid_case_id")
    case = json.loads((ROOT / "cases" / ("journey-" + case_id[1]) / case_id / "case.json").read_text())
    config = json.loads((ROOT / "suites.json").read_text())
    case["timeout_seconds"] = config["case_timeout_seconds"]
    case["model"] = config["model"]
    return case


def download(asset, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target) == asset["sha256"]:
        return
    if not asset["url"].startswith("https://"):
        raise ValueError("asset_requires_https")
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "moi-agent-journey/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(200_000_001)
    if len(data) > 200_000_000:
        raise ValueError("asset_too_large")
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(data)
    if sha256(temporary) != asset["sha256"]:
        temporary.unlink()
        raise ValueError("asset_checksum_mismatch: " + asset["path"])
    temporary.replace(target)


def fixture_path(case, fixture):
    relative = Path(fixture['path'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('unsafe_fixture_path')
    directory = (ROOT / 'cases' / ('journey-' + case['id'][1]) / case['id']).resolve()
    path = directory / relative
    if not path.resolve().is_relative_to(directory) or not path.is_file() or path.is_symlink():
        raise ValueError('invalid_fixture_path')
    if sha256(path) != fixture['sha256']:
        raise ValueError('fixture_checksum_mismatch:' + relative.as_posix())
    return path


def prepare(case, work):
    work = Path(work)
    for asset in case["assets"]:
        path = Path(asset["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe_asset_path")
        download(asset, work / "inputs" / path)
    for asset in case["oracles"]:
        download(asset, work / "oracle" / asset["path"])
    for fixture in case.get('fixtures', []):
        fixture_path(case, fixture)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("suite")
    prep = sub.add_parser("prepare")
    prep.add_argument("case_id")
    prep.add_argument("--work", default="work")
    args = parser.parse_args()
    if args.command == "plan":
        suite = load_suite(args.suite)
        # Case executors inherit the suite's declared default and may override it.
        # An explicit no-op matrix member keeps empty suites out of the denominator.
        matrix = {"include": []}
        for case_id in suite["cases"]:
            case = load_case(case_id)
            executor = case_executor(case)
            if executor not in {"office", "browser", "api"}:
                raise ValueError("invalid_case_executor:" + case_id)
            matrix["include"].append({"case": case_id, "executor": executor})
        if not matrix["include"]:
            matrix["include"].append({"case": "EMPTY", "executor": "empty"})
        output = json.dumps(matrix)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("matrix=" + output + "\n")
                f.write("concurrency=" + str(suite["concurrency"]) + "\n")
        print(output)
        return 0
    case = load_case(args.case_id)
    evidence = Evidence(Path("results") / case["id"])
    started_at, started = utc_now(), time.monotonic()
    evidence.write("preparation.json", {"status": "running", "started_at": started_at})
    try:
        prepare(case, args.work)
        evidence.write("preparation.json", {"status": "completed", "assets": case["assets"],
            "oracles": case["oracles"], "fixtures": case.get('fixtures', []), "started_at": started_at, "finished_at": utc_now(),
            "seconds": round(time.monotonic()-started,3)})
        return 0
    except Exception as error:
        evidence.write("preparation.json", {"status": "error", "reason": str(error), "type": type(error).__name__,
            "started_at": started_at,"finished_at":utc_now(),"seconds":round(time.monotonic()-started,3)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
