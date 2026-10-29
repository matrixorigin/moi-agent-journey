/** The product interaction is shared by live QA and the offline browser fixture. */

import { ScenarioFailure, openMomoConversation, requestFacts, waitVisible } from './agent-chat.mjs';

function commitPath(request) {
  if (request.method() !== 'POST') return null;
  const path = new URL(request.url()).pathname;
  const match = path.match(/\/agent-builder\/candidates\/([^/]+)\/candidate-versions\/([^/]+)\/commit$/);
  return match ? { agent_id: decodeURIComponent(match[1]), candidate_version: decodeURIComponent(match[2]) } : null;
}

/**
 * Run after the caller has established a real product session. The only
 * recorded network data are IDs and status codes needed to fetch QA evidence;
 * authentication headers, cookies, response bodies and page HTML are not saved.
 */
export async function createAgentCandidate(page, options, observe = () => {}) {
  const { workspaceId, agentName, marker, model, createPrompt, verifyPrompt, timeoutMs } = options;
  if (![workspaceId, agentName, marker, model, createPrompt, verifyPrompt].every((value) => typeof value === 'string' && value) ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError('invalid_browser_case_configuration');
  }
  const checks = [];
  let phase = 'create';
  let createdAgent = null;
  const started = Date.now();
  const remaining = () => Math.max(1, timeoutMs - (Date.now() - started));
  let notifyPageError;
  const pageError = new Promise((resolve) => { notifyPageError = resolve; });
  const record = (rule, passed, detail = '') => {
    const check = { session: 'browser', rule, passed, detail };
    checks.push(check);
    observe({ kind: 'check', ...check });
  };
  const recordRequest = (request) => {
    const facts = requestFacts(request);
    if (facts) observe({ kind: 'task_request', phase, ...facts });
    const candidate = commitPath(request);
    if (candidate) {
      let sourceDigest;
      try { sourceDigest = request.postDataJSON()?.source_digest; } catch { /* invalid request is reported by the case */ }
      observe({ kind: 'candidate_commit', ...candidate,
        source_digest: typeof sourceDigest === 'string' ? sourceDigest : null });
    }
  };
  const recordPageError = (error) => {
    observe({ kind: 'page_error', phase, error_type: error?.name ?? 'Error' });
    notifyPageError();
  };
  const withPageError = async (operation, stage) => {
    const result = await Promise.race([
      operation.then((value) => ({ value })),
      pageError.then(() => ({ pageError: true })),
    ]);
    if (result.pageError) throw new ScenarioFailure(stage, 'browser_uncaught_exception');
    return result.value;
  };
  const selectionState = async () => {
    const selector = page.getByTestId('agent-selector');
    const texts = await selector.allTextContents();
    const selected = texts.join(' ').trim();
    return { selector_count: texts.length, selected: !selected ? 'empty' :
      selected.includes(agentName) ? 'created_agent' : /momo|超级智能体/i.test(selected) ? 'momo' : 'other' };
  };
  page.on('request', recordRequest);
  page.on('pageerror', recordPageError);

  try {
    await openMomoConversation(page, workspaceId, model, remaining);

    const input = page.getByTestId('chat-input').locator('textarea');
    await waitVisible(input, remaining(), 'setup', 'chat_input_not_available');
    await input.fill(createPrompt);
    await page.getByTestId('send-btn').click();

    const card = page.getByTestId('agent-builder-candidate-card').last();
    try {
      await withPageError(waitVisible(card, remaining(), 'candidate', 'candidate_card_not_visible'), 'candidate');
      record('候选卡片可见', true);
    } catch (error) {
      if (!(error instanceof ScenarioFailure)) throw error;
      record('候选卡片可见', false, error.reason);
      throw error;
    }
    if (!(await card.getByText(agentName, { exact: true }).first().isVisible())) {
      record('候选卡片包含指定名称', false, 'candidate_name_mismatch');
      throw new ScenarioFailure('candidate', 'candidate_name_mismatch');
    }
    record('候选卡片包含指定名称', true);
    const confirm = card.getByTestId('agent-builder-confirm-btn');
    if (!(await confirm.isVisible()) || !(await confirm.isEnabled())) {
      record('确认创建按钮可用', false, 'candidate_confirm_unavailable');
      throw new ScenarioFailure('candidate', 'candidate_confirm_unavailable');
    }
    record('确认创建按钮可用', true);

    let response;
    try {
      [response] = await withPageError(Promise.all([
        page.waitForResponse((item) => Boolean(commitPath(item.request())), { timeout: remaining() }),
        confirm.click({ timeout: remaining() }),
      ]), 'commit');
    } catch (error) {
      if (error instanceof ScenarioFailure) {
        record('确认请求成功', false, error.reason);
        throw error;
      }
      if (error?.name !== 'TimeoutError') throw error;
      record('确认请求成功', false, 'candidate_commit_response_missing');
      throw new ScenarioFailure('commit', 'candidate_commit_response_missing');
    }
    const path = commitPath(response.request());
    let body;
    let digest;
    try {
      body = await response.json();
      digest = response.request().postDataJSON()?.source_digest;
    } catch {
      record('确认请求成功', false, 'candidate_commit_response_invalid');
      throw new ScenarioFailure('commit', 'candidate_commit_response_invalid', { http_status: response.status() });
    }
    if (!response.ok() || !['OK', 0].includes(body?.code) || !body?.data ||
        body.data.agent_id !== path.agent_id || body.data.workspace_id !== workspaceId || body.data.name !== agentName ||
        typeof digest !== 'string' || !digest || body.data.source_digest !== digest ||
        typeof body.data.load_version !== 'string' || !body.data.load_version) {
      record('确认请求成功', false, 'candidate_commit_failed');
      throw new ScenarioFailure('commit', 'candidate_commit_failed', { http_status: response.status() });
    }
    createdAgent = { agent_id: path.agent_id, candidate_version: path.candidate_version,
      source_digest: digest, workspace_id: workspaceId, version: body.data.load_version };
    observe({ kind: 'agent_created', ...createdAgent });
    record('确认请求成功', true);

    const openChat = card.getByTestId('agent-builder-go-to-chat-btn');
    try {
      // Switching agents is blocked by MOI while Momo's response is streaming.
      observe({ kind: 'ui_stage', stage: 'waiting_for_momo_stream_to_finish' });
      await withPageError(waitVisible(page.getByTestId('send-btn'), remaining(),
        'commit', 'momo_stream_not_finished'), 'commit');
      observe({ kind: 'ui_stage', stage: 'momo_stream_finished' });
      observe({ kind: 'ui_stage', stage: 'waiting_for_created_agent_chat_button' });
      await withPageError(waitVisible(openChat, Math.min(remaining(), 30_000),
        'commit', 'created_agent_chat_button_not_visible'), 'commit');
      observe({ kind: 'ui_stage', stage: 'created_agent_chat_button_visible' });
      await withPageError(openChat.click({ timeout: Math.min(remaining(), 30_000) }), 'commit');
      observe({ kind: 'ui_stage', stage: 'created_agent_chat_button_clicked' });
      await withPageError(page.getByTestId('agent-selector').getByText(agentName, { exact: true }).waitFor({
        state: 'visible', timeout: Math.min(remaining(), 30_000),
      }), 'commit');
      record('创建后可进入智能体会话', true);
    } catch (error) {
      if (!(error instanceof ScenarioFailure) && error?.name !== 'TimeoutError') throw error;
      const reason = error instanceof ScenarioFailure ? error.reason : 'created_agent_chat_not_available';
      const state = await selectionState();
      observe({ kind: 'ui_state', stage: 'created_agent_chat', ...state });
      record('创建后可进入智能体会话', false, `${reason}; selected=${state.selected}; selector_count=${state.selector_count}`);
      throw new ScenarioFailure('commit', reason);
    }

    phase = 'verify';
    const verifyInput = page.getByTestId('chat-input').locator('textarea');
    await verifyInput.fill(verifyPrompt);
    try {
      const [response] = await withPageError(Promise.all([
        page.waitForResponse((item) => requestFacts(item.request())?.agent_id === createdAgent.agent_id,
          { timeout: Math.min(remaining(), 30_000) }),
        page.getByTestId('send-btn').click(),
      ]), 'verify');
      if (!response.ok()) {
        throw new ScenarioFailure('verify', 'created_agent_message_failed', { http_status: response.status() });
      }
      // The final answer may differ from an intermediate streaming fragment.
      if (await withPageError(response.finished(), 'verify')) {
        throw new ScenarioFailure('verify', 'created_agent_stream_failed');
      }
      await waitVisible(page.getByTestId('send-btn'), remaining(), 'verify', 'created_agent_stream_not_finished');
      const assistant = page.getByTestId('agent-a2a-assistant-answer').last();
      await waitVisible(assistant, Math.min(remaining(), 30_000), 'verify', 'created_agent_reply_missing');
      if (!(await assistant.innerText()).includes(marker)) {
        throw new ScenarioFailure('verify', 'created_agent_reply_mismatch');
      }
    } catch (error) {
      if (!(error instanceof ScenarioFailure) && error?.name !== 'TimeoutError') throw error;
      const reason = error instanceof ScenarioFailure ? error.reason : 'created_agent_message_not_sent';
      record('新智能体可以回复', false, reason);
      throw new ScenarioFailure('verify', reason, error.detail);
    }
    record('新智能体可以回复', true);
    return { status: 'passed', checks, created_agent: createdAgent };
  } catch (error) {
    if (error instanceof ScenarioFailure) {
      return { status: 'failed', checks,
        failure: { stage: error.stage, reason: error.reason, ...error.detail }, created_agent: createdAgent };
    }
    return { status: 'error', checks, failure: { stage: 'browser', reason: error?.name ?? 'Error' },
      created_agent: createdAgent };
  } finally {
    page.off('request', recordRequest);
    page.off('pageerror', recordPageError);
  }
}
