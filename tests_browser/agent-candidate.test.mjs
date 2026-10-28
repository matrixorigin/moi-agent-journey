import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import { createAgentCandidate } from '../browser/agent-candidate.mjs';

const fixture = `<!doctype html><meta charset="utf-8"><title>Browser fixture</title>
<div data-testid="agent-chat-page">
  <div data-testid="agent-selector"><span>MOMO 超级智能体</span></div>
  <button data-testid="agent-workbench-new-chat-btn">新建对话</button>
  <button data-testid="model-selector">deepseek-v4.1-flash</button>
  <div data-testid="chat-messages"></div>
  <div data-testid="chat-input"><textarea></textarea></div>
  <button data-testid="send-btn">发送</button>
</div>
<script>
const mode = new URL(location.href).searchParams.get('mode');
let phase = 'create';
let agentName;
const q = (id) => document.querySelector('[data-testid="' + id + '"]');
if (mode === 'delayed-momo') {
  q('agent-selector').textContent = '加载中';
  setTimeout(() => { q('agent-selector').innerHTML = '<span>MOMO 超级智能体</span>'; }, 250);
}
q('send-btn').onclick = async () => {
  const text = q('chat-input').querySelector('textarea').value;
  const current = phase;
  if (mode === 'streaming' && current === 'create') {
    const button = q('send-btn');
    button.dataset.testid = 'agent-chat-stop-btn';
    setTimeout(() => { button.dataset.testid = 'send-btn'; }, 700);
  }
  const agent = current === 'create' ? 'zero' : 'ag-test';
  await fetch('/newmoi/workspaces/ws/agents/' + agent + '/a2a', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ agent_id: agent, params: { message: {
      contextId: 'conversation-' + current, messageId: 'message-' + current,
      parts: [{ kind: 'text', text }],
    } } }),
  });
  if (current === 'verify') {
    const answer = document.createElement('div');
    answer.dataset.testid = 'agent-a2a-assistant-answer';
    const content = document.createElement('span');
    content.textContent = mode === 'wrong-answer' ? 'unrelated answer' : text.split('：').at(-1);
    answer.append(content);
    q('chat-messages').append(answer);
    return;
  }
  if (mode === 'no-card') return;
  agentName = text.match(/名为 ([A-Za-z0-9_-]+)/)[1];
  const card = document.createElement('section');
  card.dataset.testid = 'agent-builder-candidate-card';
  const name = document.createElement('span');
  name.textContent = agentName;
  const confirm = document.createElement('button');
  confirm.dataset.testid = 'agent-builder-confirm-btn';
  confirm.textContent = '确认创建';
  confirm.onclick = async () => {
    if (mode === 'js-error') throw new Error('fixture browser exception');
    const response = await fetch('/newmoi/workspaces/ws/agent-builder/candidates/ag-test/candidate-versions/v1/commit', { method: 'POST' });
    if (!response.ok) return;
    if (mode === 'no-chat') return;
    const open = document.createElement('button');
    open.dataset.testid = 'agent-builder-go-to-chat-btn';
    open.textContent = '去聊天';
    open.onclick = () => {
      if (mode === 'streaming' && q('agent-chat-stop-btn')) return;
      phase = 'verify';
      q('agent-selector').textContent = '';
      const selected = document.createElement('span');
      selected.textContent = agentName;
      q('agent-selector').append(selected);
      q('chat-messages').replaceChildren();
    };
    card.append(open);
  };
  card.append(name, confirm);
  q('chat-messages').append(card);
};
</script>`;

let server;
let baseURL;
let browser;
let fixtureMode = 'success';

before(async () => {
  server = http.createServer((request, response) => {
    if (request.url.includes('/candidate-versions/')) {
      const isError = fixtureMode === 'commit-error';
      response.writeHead(isError ? 500 : 200, { 'content-type': 'application/json' });
      response.end(JSON.stringify(isError ? { code: 'INTERNAL' } : {
        code: 0, data: { agent_id: 'ag-test', workspace_id: 'ws', load_version: '1.0.0' },
      }));
    } else if (request.url.endsWith('/a2a')) {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end('{}');
    } else {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      response.end(fixture.replace("const mode = new URL(location.href).searchParams.get('mode');", `const mode = ${JSON.stringify(fixtureMode)};`));
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseURL = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
});

async function run(mode) {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  fixtureMode = mode;
  const events = [];
  try {
    const result = await createAgentCandidate(page, {
      workspaceId: 'ws', agentName: 'J2-Test-42', marker: 'J2-MARKER-42',
      model: 'deepseek-v4.1-flash', createPrompt: '请创建一个名为 J2-Test-42 的智能体。',
      verifyPrompt: '请只回复这个标记：J2-MARKER-42', timeoutMs: 1800,
    }, (event) => events.push(event));
    return { result, events };
  } finally {
    await context.close();
  }
}

test('creates the agent through the UI and observes both real browser requests', async () => {
  const { result, events } = await run('success');
  assert.equal(result.status, 'passed');
  assert.equal(result.created_agent.agent_id, 'ag-test');
  assert.equal(result.checks.length, 6);
  assert.ok(result.checks.every((item) => item.passed));
  assert.deepEqual(events.filter((event) => event.kind === 'task_request').map((event) => event.phase), ['create', 'verify']);
});

test('waits for the default agent to load before starting the case', async () => {
  const { result } = await run('delayed-momo');
  assert.equal(result.status, 'passed');
});

test('waits for the Momo stream to finish before switching agents', async () => {
  const { result, events } = await run('streaming');
  assert.equal(result.status, 'passed');
  assert.ok(events.findIndex((event) => event.stage === 'momo_stream_finished') <
            events.findIndex((event) => event.stage === 'created_agent_chat_button_clicked'));
});

test('reports a missing candidate as a product failure', async () => {
  const { result, events } = await run('no-card');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'candidate_card_not_visible');
  assert.equal(result.checks.at(-1).passed, false);
  assert.equal(events.filter((event) => event.kind === 'task_request').length, 1);
});

test('reports the commit API failure instead of claiming an agent was created', async () => {
  const { result } = await run('commit-error');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'candidate_commit_failed');
  assert.equal(result.failure.http_status, 500);
  assert.equal(result.created_agent, null);
});

test('reports an uncaught click error as a product failure', async () => {
  const { result, events } = await run('js-error');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'browser_uncaught_exception');
  assert.ok(events.some((event) => event.kind === 'page_error'));
});

test('reports a missing go-to-chat button after a successful commit', async () => {
  const { result } = await run('no-chat');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'created_agent_chat_button_not_visible');
  assert.equal(result.created_agent.agent_id, 'ag-test');
});

test('rejects a completed response that does not contain the requested marker', async () => {
  const { result } = await run('wrong-answer');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'created_agent_reply_mismatch');
});
