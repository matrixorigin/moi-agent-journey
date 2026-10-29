import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import { selectKnowledgeInChat } from '../browser/chat-knowledge.mjs';

const fixture = `<!doctype html><meta charset="utf-8">
<div data-testid="agent-chat-page">
  <div data-testid="agent-selector"><span>MOMO 超级智能体</span></div>
  <button data-testid="agent-workbench-new-chat-btn">新建对话</button>
  <button data-testid="model-selector">model</button>
  <div data-testid="chat-input"><textarea></textarea></div>
  <button data-testid="plus-btn">+</button>
  <div id="menu" hidden>
    <button data-testid="home-picker-knowledge">知识库</button>
    <div id="picker" hidden>
      <input data-testid="home-picker-knowledge-search">
      <div role="checkbox" aria-checked="false" data-testid="home-picker-knowledge-item-42">Fern</div>
      <div role="checkbox" aria-checked="false" data-testid="home-picker-knowledge-item-84">Birch</div>
    </div>
  </div>
  <button data-testid="send-btn">发送</button>
  <div id="messages"></div>
</div>
<script>
const mode = MODE;
const q = (id) => document.querySelector('[data-testid="' + id + '"]');
q('plus-btn').onclick = () => { document.querySelector('#menu').hidden = !document.querySelector('#menu').hidden; };
q('home-picker-knowledge').onclick = () => { document.querySelector('#picker').hidden = false; };
for (const id of ['42', '84']) q('home-picker-knowledge-item-' + id).onclick = (event) => {
  event.currentTarget.setAttribute('aria-checked', 'true');
};
q('send-btn').onclick = async () => {
  const ids = mode === 'missing-scope' ? ['42'] : ['42', '84'];
  await fetch('/newmoi/workspaces/ws/agents/zero/a2a', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({agent_id: 'zero', agent_workspace_id: 'system', params: {message: {
      contextId: 'conversation', messageId: 'message', metadata: {semantic_model_ids: ids},
    }}}),
  });
  const answer = document.createElement('div');
  answer.dataset.testid = 'agent-a2a-assistant-answer';
  answer.textContent = mode === 'wrong-answer' ? 'FERN-WALNUT' : 'FERN-WALNUT BIRCH-LEMON';
  const sources = document.createElement('div');
  sources.dataset.testid = 'agent-answer-sources';
  for (const name of mode === 'missing-source' ? ['fern.txt'] : ['fern.txt', 'birch.txt']) {
    const source = document.createElement('button'); source.textContent = name;
    source.onclick = () => {
      if (mode === 'broken-preview') return;
      const preview = document.createElement('div'); preview.dataset.testid = 'file-preview-content';
      preview.textContent = 'Project Fern: FERN-WALNUT'; document.querySelector('#messages').append(preview);
    };
    sources.append(source);
  }
  document.querySelector('#messages').append(answer, sources);
};
</script>`;

let server, baseURL, browser, mode;
before(async () => {
  server = http.createServer((request, response) => {
    if (request.url.endsWith('/a2a')) {
      response.writeHead(200, { 'content-type': 'application/json' }); response.end('{}');
    } else {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      response.end(fixture.replace('MODE', JSON.stringify(mode)));
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

async function run(nextMode, expectedBoth = true) {
  mode = nextMode;
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  const events = [];
  try {
    const result = await selectKnowledgeInChat(page, { workspaceId: 'ws', model: 'model',
      prompt: '请回答问题并引用来源', timeoutMs: 3000, knowledge: [
        { id: '42', name: 'Fern', file: 'fern.txt', marker: 'FERN-WALNUT', expected: true },
        { id: '84', name: 'Birch', file: 'birch.txt', marker: 'BIRCH-LEMON', expected: expectedBoth },
      ] }, (event) => events.push(event));
    return { result, events };
  } finally { await context.close(); }
}

test('selects both knowledge bases and verifies the actual message and displayed sources', async () => {
  const { result, events } = await run('success');
  assert.equal(result.status, 'passed');
  assert.ok(result.checks.every((check) => check.passed));
  assert.deepEqual(events.find((event) => event.kind === 'task_request').knowledge_ids, ['42', '84']);
});

test('fails when the page drops a selected knowledge base from the message', async () => {
  const { result } = await run('missing-scope');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'selected_knowledge_missing_from_request');
});

test('selects two sources while answering a simple question from one of them', async () => {
  const { result, events } = await run('wrong-answer', false);
  assert.equal(result.status, 'passed');
  assert.deepEqual(events.find((event) => event.kind === 'task_request').knowledge_ids, ['42', '84']);
});

test('fails when an answer or a displayed source is missing', async () => {
  for (const failure of ['wrong-answer', 'missing-source']) {
    const { result } = await run(failure);
    assert.equal(result.status, 'failed');
    assert.ok(result.checks.some((check) => check.passed === false));
  }
});

test('fails if a visible citation cannot open its source', async () => {
  const { result } = await run('broken-preview');
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.reason, 'knowledge_preview_not_visible');
});
