/** Exercise knowledge selection, the sent scope, the answer and its visible sources. */
import { ScenarioFailure, openMomoConversation, requestFacts, waitVisible } from './agent-chat.mjs';

export async function selectKnowledgeInChat(page, options, observe = () => {}) {
  const { workspaceId, model, prompt, knowledge, timeoutMs } = options;
  if (![workspaceId, model, prompt].every((value) => typeof value === 'string' && value) ||
      !Array.isArray(knowledge) || !knowledge.length ||
      knowledge.some((item) => !item || typeof item.expected !== 'boolean' || ![item.id, item.name, item.file, item.marker]
        .every((value) => typeof value === 'string' && value)) || !knowledge.some((item) => item.expected) ||
      new Set(knowledge.map((item) => item.id)).size !== knowledge.length ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError('invalid_browser_case_configuration');
  }
  const checks = [];
  const expected = knowledge.filter((item) => item.expected);
  const started = Date.now();
  const remaining = () => Math.max(1, timeoutMs - (Date.now() - started));
  let request = null;
  let pageError = null;
  let answerDelivered = false;
  let stage = 'setup';
  const record = (rule, passed, detail = '') => {
    const check = { session: 'browser', rule, passed, detail };
    checks.push(check);
    observe({ kind: 'check', ...check });
  };
  const onRequest = (item) => {
    const facts = requestFacts(item);
    if (facts) {
      request = facts;
      observe({ kind: 'task_request', phase: 'query', ...facts });
    }
  };
  const onPageError = (error) => {
    pageError = error?.name ?? 'Error';
    observe({ kind: 'page_error', phase: 'query', error_type: pageError });
  };
  page.on('request', onRequest);
  page.on('pageerror', onPageError);
  try {
    await openMomoConversation(page, workspaceId, model, remaining);
    const input = page.getByTestId('chat-input').locator('textarea');
    await waitVisible(input, remaining(), 'setup', 'chat_input_not_available');
    await input.fill(prompt);
    stage = 'selection';
    await page.getByTestId('plus-btn').click();
    await page.getByTestId('home-picker-knowledge').click();
    const search = page.getByTestId('home-picker-knowledge-search');
    await waitVisible(search, remaining(), 'selection', 'knowledge_picker_unavailable');
    for (const item of knowledge) {
      await search.fill(item.name);
      const selected = page.getByTestId(`home-picker-knowledge-item-${item.id}`);
      await waitVisible(selected, Math.min(remaining(), 30_000), 'selection', 'knowledge_option_missing');
      await selected.click();
      await waitVisible(page.locator(`[data-testid="home-picker-knowledge-item-${item.id}"][aria-checked="true"]`),
        Math.min(remaining(), 10_000), 'selection', 'knowledge_option_not_selected');
      record(`输入框选中知识库 ${item.name}`, true);
    }
    stage = 'submit';
    observe({ kind: 'ui_stage', stage });
    const [response] = await Promise.all([
      page.waitForResponse((item) => Boolean(requestFacts(item.request())), { timeout: remaining() }),
      page.getByTestId('send-btn').click({ timeout: remaining() }),
    ]);
    if (!response.ok()) throw new ScenarioFailure('query', 'knowledge_message_failed', { http_status: response.status() });
    if (!request || knowledge.some((item) => !request.knowledge_ids.includes(item.id))) {
      record('发送请求包含选择的知识库', false, 'selected_knowledge_missing_from_request');
      throw new ScenarioFailure('query', 'selected_knowledge_missing_from_request');
    }
    record('发送请求包含选择的知识库', true);
    stage = 'stream';
    if (await response.finished()) throw new ScenarioFailure('query', 'knowledge_stream_failed');
    await waitVisible(page.getByTestId('send-btn'), remaining(), 'query', 'knowledge_stream_not_finished');
    const answer = page.getByTestId('agent-a2a-assistant-answer').last();
    stage = 'answer';
    await waitVisible(answer, Math.min(remaining(), 30_000), 'query', 'knowledge_answer_missing');
    const answerText = await answer.innerText();
    answerDelivered = Boolean(answerText.trim());
    for (const item of expected) {
      const found = answerText.includes(item.marker);
      record(`页面答复包含 ${item.file} 的答案`, found, found ? '' : 'knowledge_answer_mismatch');
    }
    const sources = page.getByTestId('agent-answer-sources').last();
    stage = 'citation';
    await waitVisible(sources, Math.min(remaining(), 20_000), 'citation', 'knowledge_sources_not_visible');
    for (const item of expected) {
      const visible = await sources.getByText(item.file, { exact: true }).isVisible();
      record(`页面展示 ${item.file} 引用`, visible, visible ? '' : 'knowledge_source_not_visible');
    }
    const firstSource = sources.getByText(expected[0].file, { exact: true });
    if (await firstSource.isVisible()) {
      stage = 'citation_preview';
      await firstSource.click();
      const preview = page.getByTestId('file-preview-content');
      await waitVisible(preview, Math.min(remaining(), 20_000), 'citation_preview', 'knowledge_preview_not_visible');
      await waitVisible(preview.getByText(expected[0].marker, { exact: false }), Math.min(remaining(), 20_000),
        'citation_preview', 'knowledge_preview_content_missing');
      record(`引用可打开并展示 ${expected[0].file} 的内容`, true);
    }
    if (pageError) throw new ScenarioFailure('browser', 'browser_uncaught_exception', { error_type: pageError });
    return { status: checks.every((item) => item.passed) ? 'passed' : 'failed', checks, answer_delivered: answerDelivered,
      ...(checks.some((item) => !item.passed) ? { failure: { stage: 'validation', reason: 'acceptance_checks_failed' } } : {}) };
  } catch (error) {
    const send = page.getByTestId('send-btn');
    const input = page.getByTestId('chat-input').locator('textarea');
    const inputValue = await input.inputValue({ timeout: 1000 }).catch(() => null);
    observe({ kind: 'ui_state', stage, send_visible: await send.isVisible().catch(() => null),
      send_enabled: await send.isEnabled({ timeout: 1000 }).catch(() => null),
      prompt_length: inputValue === null ? null : inputValue.length,
      picker_visible: await page.getByTestId('home-picker-knowledge-search').isVisible().catch(() => null) });
    if (error instanceof ScenarioFailure || error?.name === 'TimeoutError') {
      if (!checks.some((item) => !item.passed)) {
        record('页面交互完成', false, error.reason ?? 'browser_interaction_timeout');
      }
      return { status: 'failed', checks, answer_delivered: answerDelivered, failure: { stage: error.stage ?? stage,
        reason: error.reason ?? 'browser_interaction_timeout', ...(error.detail ?? {}) } };
    }
    return { status: 'error', checks, answer_delivered: answerDelivered, failure: { stage: 'browser', reason: error?.name ?? 'Error' } };
  } finally {
    page.off('request', onRequest);
    page.off('pageerror', onPageError);
  }
}
