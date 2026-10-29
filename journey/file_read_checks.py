"""Require actual retrieval and a decoder failure for deliberately damaged input."""
import json
import re

from .client import Failure


def evaluate_file_rejection(path, task_id, session_id, upload):
    file_id, name = upload.get('file_id'), upload.get('original_name')
    if not isinstance(file_id,str) or not file_id or name not in {'broken.docx','broken.zip','broken.png'}:
        raise Failure('invalid_damaged_file_fixture')
    calls, downloaded, attempted, rejected = {}, False, False, False
    decoder = r'(?:Image\.open|PIL)' if name.endswith('.png') else r'(?:zipfile\.ZipFile|moi-docx\s+inspect|unzip\b)'
    error = r'(?:Truncated File Read|UnidentifiedImageError|cannot identify image|broken data stream)' if name.endswith('.png') else r'(?:BadZipFile|not a zip file|invalid ZIP package|cannot read ZIP package|End-of-central-directory)'
    for line in path.read_text().splitlines():
        event=json.loads(line);payload=event.get('payload')
        if event.get('task_id')!=task_id or not isinstance(payload,dict) or payload.get('tool_id')!='mcp__moi-tools__bash':
            continue
        call_id=payload.get('call_id')
        if event.get('kind')=='tool_call':
            arguments=payload.get('arguments');command=arguments.get('command') if isinstance(arguments,dict) else None
            if isinstance(command,str):calls[call_id]=command
        elif event.get('kind')=='tool_result' and call_id in calls:
            command=calls[call_id];wrapper=payload.get('output')
            output=wrapper.get('output') if isinstance(wrapper,dict) else None
            if not isinstance(output,dict) or type(output.get('exit_code')) is not int:
                continue
            if file_id in command and name in command and 'moi-cli' in command and re.search(r'\bdownload\b',command):
                downloaded |= payload.get('success') is True and output['exit_code']==0
            if name in command and re.search(decoder,command):
                attempted=True
                text=str(output.get('stdout',''))+'\n'+str(output.get('stderr',''))
                rejected |= downloaded and bool(re.search(error,text,re.I)) and output.get('output_truncated') is not True
    return [
        {'session':session_id,'rule':'实际读取本次损坏附件并尝试解码','passed':downloaded and attempted,
         'detail':'' if downloaded and attempted else 'damaged_file_decode_not_observed'},
        {'session':session_id,'rule':'解码器对本次附件返回格式损坏错误','passed':rejected,
         'detail':'' if rejected else 'damaged_file_error_not_observed'},
    ]
