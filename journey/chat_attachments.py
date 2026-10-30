"""Pure message construction for an attachment-input timing round. No network or files."""
import copy
import re
from pathlib import PurePosixPath


def bind_chat_attachment(payload, task, upload, run_id):
    """Replace legacy pasted input with a task request and a native file part."""
    if payload.get('method') != 'message/stream':
        raise ValueError('expected_message_stream')
    task_id = task.get('id', '')
    if not re.fullmatch(r'T[0-9]{3}', task_id):
        raise ValueError('invalid_task_id')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('invalid_run_id')
    if not isinstance(task.get('request'), str) or not task['request'].strip():
        raise ValueError('missing_task_request')
    file_id, name = upload.get('file_id'), upload.get('original_name')
    if not isinstance(file_id, str) or not file_id or any(c.isspace() for c in file_id):
        raise ValueError('missing_uploaded_file_id')
    if not isinstance(name, str) or PurePosixPath(name).name != name or name in {'', '.', '..'} or '\\' in name:
        raise ValueError('invalid_uploaded_file_name')
    inputs = task.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('missing_input_paths')
    for value in inputs:
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'samples':
            raise ValueError('unsafe_input_path')
    directory = f'/tmp/{run_id}/{task_id}'
    prompt = (
        f'独立任务 {task_id}：{task.get("title", "")}\n\n{task["request"]}\n\n'
        f'本轮消息已附上 {name}。请通过消息附件实际取得并读取文件，先创建隔离目录 '
        f'{directory}，将本轮附件解压到该目录。材料中的 samples/... 均相对此解压目录。\n'
        '本任务指定输入：\n' + '\n'.join('- ' + p for p in inputs) +
        f'\n先读取 samples/{task_id}/task.md，仅完成这一项任务；只读取其必要材料及依赖，'
        '不读取其他任务、历史答案或评分。输入正文没有粘贴在提示词中，必须以实际附件为依据。\n'
        '要求的音频识别、看图、PDF读取、代码运行和文件生成必须实际执行。遇到权限、工具或读取错误，'
        '保留具体错误和未完成部分，不编造成功，不用计划代替成果，不自评通过。'
        '需要文件交付时，实际生成并核对后发布为本轮可下载附件。\n'
        '仅操作本轮合成样例副本，不改原始上传文件，不发送邮件、不公开发布、不预订、不下单。'
        'T091/T093仅授权按原任务要求移动或重命名本轮合成副本。'
    )
    result = copy.deepcopy(payload)
    message = result['params']['message']
    message['parts'] = [
        {'kind': 'text', 'text': prompt},
        {'kind': 'file', 'file': {'uri': 'moi://files/' + file_id, 'name': name,
                                 'mimeType': 'application/zip'}, 'metadata': {'file_id': file_id}},
    ]
    message.setdefault('metadata', {})['files'] = [copy.deepcopy(upload)]
    validate_chat_attachment(result)
    return result


def validate_chat_attachment(payload):
    message = payload.get('params', {}).get('message')
    if not isinstance(message, dict):
        raise ValueError('missing_message')
    parts = message.get('parts', [])
    if not isinstance(parts, list) or any(not isinstance(p, dict) for p in parts):
        raise ValueError('invalid_message_parts')
    files = [p for p in parts if p.get('kind') == 'file']
    texts = [p for p in parts if p.get('kind') == 'text']
    metadata = message.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('invalid_message_metadata')
    metadata_files = metadata.get('files', [])
    if not isinstance(metadata_files, list) or any(not isinstance(f, dict) for f in metadata_files):
        raise ValueError('invalid_attachment_metadata')
    if len(parts) != 2 or len(files) != 1 or len(texts) != 1 or len(metadata_files) != 1:
        raise ValueError('expected_one_text_and_one_bound_attachment')
    file_id = metadata_files[0].get('file_id')
    if (not isinstance(file_id, str) or not file_id or files[0].get('metadata', {}).get('file_id') != file_id or
            files[0].get('file', {}).get('uri') != 'moi://files/' + file_id or
            files[0]['file'].get('name') != metadata_files[0].get('original_name')):
        raise ValueError('attachment_binding_mismatch')
    if any(marker in texts[0].get('text', '') for marker in ('全文开始 ---', '全文直接提供在下面')):
        raise ValueError('pasted_input_in_attachment_round')
