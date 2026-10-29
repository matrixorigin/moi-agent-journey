"""Read back only the Slack/Feishu message IDs returned by the Agent's send."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .client import Failure, NoRedirect
from .builder_flow import ProductCaseFailure


def provider_json(execution, url, *, token=None, body=None):
    headers = {'Accept':'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        headers['Content-Type']='application/json'
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    remaining = min(20, execution.client.deadline - time.monotonic())
    if remaining <= 0:
        raise Failure('deadline_exceeded')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=remaining) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise Failure('receiver_response_too_large')
        value=json.loads(raw)
        if not isinstance(value, dict):
            raise Failure('receiver_invalid_response')
        return value
    except urllib.error.HTTPError as error:
        status=error.code
        error.close()
        raise Failure('receiver_http_error', status) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise Failure('receiver_unavailable') from None


def verify_receiver(execution, provider, output, marker, expected_type, session_id, checks):
    if provider == 'slack':
        query=urllib.parse.urlencode({'channel':output['channel_id'],'oldest':output['timestamp'],
                                     'latest':output['timestamp'],'inclusive':'true','limit':1})
        data=provider_json(execution,'https://slack.com/api/conversations.history?' + query,
                           token=os.environ['MOI_J2_SLACK_READ_TOKEN'])
        messages=data.get('messages',[])
        valid=(data.get('ok') is True and len(messages)==1 and messages[0].get('ts')==output['timestamp'] and
               marker in json.dumps(messages[0],ensure_ascii=False) and
               (expected_type=='text' or isinstance(messages[0].get(expected_type),list) and bool(messages[0][expected_type])))
    elif provider == 'feishu':
        token=getattr(execution,'feishu_read_token',None)
        if token is None:
            auth=provider_json(execution,'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                body={'app_id':os.environ['MOI_J2_FEISHU_APP_ID'],'app_secret':os.environ['MOI_J2_FEISHU_APP_SECRET']})
            token=auth.get('tenant_access_token')
            if auth.get('code') != 0 or not isinstance(token,str) or not token:
                raise Failure('feishu_receiver_auth_failed')
            execution.feishu_read_token=token
        data=provider_json(execution,'https://open.feishu.cn/open-apis/im/v1/messages/' +
                           urllib.parse.quote(output['message_id'],safe=''),token=token)
        messages=data.get('data',{}).get('items',[])
        valid=(data.get('code')==0 and len(messages)==1 and messages[0].get('message_id')==output['message_id'] and
               messages[0].get('chat_id')==output['chat_id'] and messages[0].get('msg_type')==expected_type and
               marker in json.dumps(messages[0].get('body'),ensure_ascii=False))
    else:
        raise Failure('unsupported_receiver_case')
    execution.evidence.append('receiver-readback.jsonl',{'provider':provider,'receipt':output,'response':data})
    checks.append({'session':session_id,'rule':'接收端回读本次消息的身份、类型和内容',
                   'passed':bool(valid),'detail':'' if valid else 'receiver_message_mismatch'})
    execution.report['validation']={'checks':checks}
    execution.checkpoint()
    if not valid:
        raise ProductCaseFailure('receiver_message_mismatch')
