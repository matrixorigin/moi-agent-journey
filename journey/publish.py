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
    for file in sorted(source.rglob('*')):
        relative=file.relative_to(source)
        parts=relative.parts
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
