/** Shared agent chat navigation and request observation. */

export class ScenarioFailure extends Error {
  constructor(stage, reason, detail = {}) {
    super(reason);
    this.stage = stage;
    this.reason = reason;
    this.detail = detail;
  }
}

export function requestFacts(request) {
  if (request.method() !== 'POST') return null;
  const url = new URL(request.url());
  if (!/(?:^|\/)agents(?:\/[^/]+)?\/a2a$/.test(url.pathname)) return null;
  let body;
  try {
    body = request.postDataJSON();
  } catch {
    return null;
  }
  const message = body?.params?.message;
  if (!message || typeof message.contextId !== 'string' || !message.contextId) return null;
  const match = url.pathname.match(/\/agents\/([^/]+)\/a2a$/);
  return {
    conversation_id: message.contextId,
    message_id: typeof message.messageId === 'string' ? message.messageId : null,
    agent_id: typeof body.agent_id === 'string' ? body.agent_id : match ? decodeURIComponent(match[1]) : null,
    agent_workspace_id: typeof body.agent_workspace_id === 'string' ? body.agent_workspace_id : null,
    knowledge_ids: Array.isArray(message.metadata?.semantic_model_ids) ?
      message.metadata.semantic_model_ids.map(String) : [],
  };
}

export async function waitVisible(locator, timeoutMs, stage, reason) {
  try {
    await locator.waitFor({ state: 'visible', timeout: timeoutMs });
  } catch (error) {
    if (error?.name === 'TimeoutError') throw new ScenarioFailure(stage, reason);
    throw error;
  }
}

export async function openMomoConversation(page, workspaceId, model, remaining) {
  await page.goto(`/${encodeURIComponent(workspaceId)}/agent`, { waitUntil: 'domcontentloaded', timeout: remaining() });
  await waitVisible(page.getByTestId('agent-chat-page'), remaining(), 'setup', 'agent_page_not_available');
  const agentSelector = page.getByTestId('agent-selector');
  const newChat = page.getByTestId('agent-workbench-new-chat-btn');
  await waitVisible(newChat, remaining(), 'setup', 'new_conversation_unavailable');
  await newChat.click();
  await waitVisible(agentSelector, remaining(), 'setup', 'agent_selector_not_available');
  await waitVisible(agentSelector.getByText(/momo|超级智能体/i), Math.min(remaining(), 30_000),
    'setup', 'momo_not_selected');
  const modelSelector = page.getByTestId('model-selector');
  await waitVisible(modelSelector, remaining(), 'setup', 'model_selector_unavailable');
  if (!(await modelSelector.innerText()).includes(model)) {
    await modelSelector.click();
    const modelOption = page.locator('[data-testid^="model-dd-"]').filter({ hasText: model }).first();
    await waitVisible(modelOption, remaining(), 'setup', 'configured_model_unavailable');
    await modelOption.click();
    if (!(await modelSelector.innerText()).includes(model)) {
      throw new ScenarioFailure('setup', 'configured_model_not_selected');
    }
  }
}
