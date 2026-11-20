"""Build the public download without losing traces when a generated file is unsafe."""
import argparse
import json
from pathlib import Path

from .evidence import Evidence, configured_secrets, sha256
from .report import ArtifactRejected,inspect_payload


def redact_record(data, suffix, evidence):
    """Redact decoded values so quoted code examples cannot break JSON escaping."""
    text = data.decode('utf-8')
    if suffix == '.json':
        value = json.loads(text)
        cleaned = evidence.clean(value)
        return (json.dumps(cleaned, ensure_ascii=False, indent=2) + '\n').encode() if cleaned != value else data
    if suffix == '.jsonl':
        lines = []
        for line in text.splitlines(keepends=True):
            if line.strip():
                value = json.loads(line)
                cleaned = evidence.clean(value)
                if cleaned != value:
                    line = json.dumps(cleaned, ensure_ascii=False) + ('\n' if line.endswith('\n') else '')
            lines.append(line)
        return ''.join(lines).encode()
    return evidence.text(text).encode()


def prepare_publication(source,destination,secrets):
    source,destination=Path(source),Path(destination)
    public=Evidence(destination,secrets)
    withheld=[]
    private_cases=set()
    from .catalog import load_case
    from .benchmark_metrics import model_rounds, public_result
    from .report import _valid_case_result, case_summary
    for case in source.iterdir():
        if not case.is_dir():continue
        definition=load_case(case.name)
        # The workspace now contains production business data. Even an older
        # synthetic case may enumerate it, so every J3 runtime report uses the
        # same metrics-only boundary.
        if definition.get('workspace')!='journey-3':continue
        private_cases.add(case.name)
        result=json.loads((case/'result.json').read_text())
        from .rag_report import valid_rag_result
        if (result.get('case_id')!=case.name or result.get('workspace')!='journey-3' or
                not _valid_case_result(result) or not valid_rag_result(result.get('rag'))):
            raise ValueError('private_case_result_invalid')
        projected=public_result(result)
        projected['model_round_count'] = model_rounds(case/'events.jsonl', result.get('tasks', []))
        receipt = {}
        try:
            receipt = json.loads((case/'history-receipt.json').read_text())
        except (OSError, ValueError):
            pass
        import re
        if (not isinstance(receipt, dict) or receipt.get('state') not in {'saved', 'already_saved'} or
                not re.fullmatch(r'[a-f0-9]{64}', receipt.get('result_key', ''))):
            projected['test_status']='error'
            projected['harness_errors'].append({'stage':'history','reason':'QA_history_not_saved'})
        if not _valid_case_result(projected) or not valid_rag_result(projected.get('rag')):
            raise ValueError('private_public_projection_invalid')
        safe=Evidence(destination/case.name,secrets)
        safe.write('result.json',projected)
        (safe.root/'case-summary.md').write_text(case_summary(projected))
        safe.write('publication.json',{'status':'metrics_only','policy':'private_details_stay_in_workspace'})
    for file in sorted(source.rglob('*')):
        relative=file.relative_to(source)
        parts=relative.parts
        if parts[0] in private_cases:continue
        if file.is_symlink():
            withheld.append({'path':relative.as_posix(),'reason':'artifact_symlink_not_allowed','sha256':None})
            continue
        if not file.is_file():continue
        # These are outputs of the agent/renderer, not trusted recorder documents.
        payload=('outputs' in parts or 'rendered' in parts or
                 ('artifacts' in parts and file.name!='inventory.json'))
        data=file.read_bytes()
        if not payload:
            # Recorder files are UTF-8 JSON/JSONL/logs. Apply the same credential
            # redaction at this publication boundary, preserving every event.
            data=redact_record(data, file.suffix, public)
        try:
            inspect_payload(relative.as_posix().encode(),public.secrets)
            inspect_payload(data,public.secrets,name=file.name)
        except ArtifactRejected as error:
            withheld.append({'path':relative.as_posix(),'reason':str(error),'sha256':sha256(file),'bytes':file.stat().st_size})
            continue
        target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    # Each affected case retains its original verdict and all safe trace records.
    # Authentication-protected QA file IDs remain in its artifact inventory.
    for case in source.iterdir():
        if not case.is_dir():continue
        if case.name in private_cases:continue
        rejected=[item for item in withheld if Path(item['path']).parts[0]==case.name]
        case_public=Evidence(destination/case.name,public.secrets)
        case_public.write('publication.json',{'status':'partial' if rejected else 'complete',
            'withheld_files':rejected,'policy':'uninspectable_files_are_not_public;safe_records_are_retained'})
        result_path=case_public.root/'result.json'
        if result_path.exists():
            result=json.loads(result_path.read_text())
            result['publication']={'status':'partial' if rejected else 'complete','withheld_files':rejected}
            case_public.write('result.json',result)
    return withheld


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='results')
    parser.add_argument('--out',default='published-results')
    args=parser.parse_args()
    withheld=prepare_publication(args.root, args.out, configured_secrets())
    print(json.dumps({'publication':'partial' if withheld else 'complete','withheld_file_count':len(withheld)}))
    return 0


if __name__=='__main__':raise SystemExit(main())
