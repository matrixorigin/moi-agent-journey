/** Fresh, non-persistent browser session for one QA case. */
import fs from 'node:fs';
import { chromium } from 'playwright';
import { createAgentCandidate } from './agent-candidate.mjs';

const config = JSON.parse(process.env.JOURNEY_WORKER_CONFIG);
const output = fs.openSync(process.env.JOURNEY_OBSERVATIONS, 'a', 0o600);
const observe = (event) => fs.writeSync(output, JSON.stringify({ recorded_at: new Date().toISOString(), ...event }) + '\n');
let browser;
let stage = 'browser_launch';
try {
  const uiOrigin = new URL(process.env.MOI_UI_BASE_URL).origin;
  const authOrigin = new URL(process.env.MOI_UI_AUTH_ORIGIN).origin;
  if (!process.env.MOI_UI_ACCOUNT || !process.env.MOI_UI_PASSWORD ||
      uiOrigin !== process.env.MOI_UI_BASE_URL || authOrigin !== process.env.MOI_UI_AUTH_ORIGIN ||
      !uiOrigin.startsWith('https://') || !authOrigin.startsWith('https://')) {
    throw new Error('invalid_ui_configuration');
  }
  // Browser child processes do not need the Actions credentials.
  const childEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !key.startsWith('MOI_') && !key.startsWith('JOURNEY_')));
  browser = await chromium.launch({ headless: true, env: childEnv });
  const context = await browser.newContext({ baseURL: uiOrigin });
  const page = await context.newPage();
  stage = 'open_login';
  await page.goto('/', { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.getByTestId('domestic-login-form').waitFor({ state: 'visible', timeout: 60_000 });
  if (new URL(page.url()).origin !== authOrigin) throw new Error('unexpected_login_origin');
  stage = 'select_email';
  await page.getByTestId('domestic-login-method-switch').getByText('邮箱', { exact: true }).click();
  stage = 'fill_login';
  await page.getByTestId('domestic-login-email-input').fill(process.env.MOI_UI_ACCOUNT);
  await page.getByTestId('domestic-login-email-password-input').fill(process.env.MOI_UI_PASSWORD);
  await page.getByTestId('domestic-login-email-policy').check();
  stage = 'submit_login';
  await Promise.all([
    page.waitForURL((url) => url.origin === uiOrigin, { timeout: 60_000 }),
    page.getByTestId('domestic-login-email-submit').click(),
  ]);
  observe({ kind: 'login', status: 'completed' });
  stage = 'scenario';
  const result = await createAgentCandidate(page, config, observe);
  observe({ kind: 'result', ...result });
} catch (error) {
  // Never print page content, URLs, credentials, or exception messages.
  observe({ kind: 'result', status: 'error', checks: [],
    failure: { stage, reason: error?.message === 'unexpected_login_origin' ?
      'unexpected_login_origin' : error?.message === 'invalid_ui_configuration' ?
      'invalid_ui_configuration' : `${stage}_${error?.name ?? 'Error'}` }, blocked: stage !== 'scenario' });
} finally {
  await browser?.close();
  fs.closeSync(output);
}
