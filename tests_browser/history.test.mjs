import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { chromium } from 'playwright';

test('history separates fixed model series, keeps score gaps and handles an empty database', async () => {
  const template = await readFile(new URL('../datasets/journey-3/history.html', import.meta.url), 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const empty = await browser.newPage();
    const errors = [];
    empty.on('pageerror', error => errors.push(error.message));
    await empty.setContent(template);
    assert.match(await empty.locator('#coverage').innerText(), /0 次记录/);
    await empty.close();
    const base = { case_id: 'J3-88', case_revision: 'r1', snapshot_id: 's1',
      evaluator_version: 'judge1', model_key: 'a'.repeat(32), task_state: 'completed',
      tool_call_count: 2, model_round_count: 3, benchmark_state: 'eligible' };
    const data = [
      { ...base, evaluated_at: '2026-11-17T00:00:00Z', duration_seconds: 30, quality_score: 75 },
      { ...base, evaluated_at: '2026-11-17T06:00:00Z', duration_seconds: 20, quality_score: null },
      { ...base, evaluated_at: '2026-11-18T00:00:00Z', duration_seconds: 10, quality_score: 100 },
      { ...base, model_key: 'b'.repeat(32), evaluated_at: '2026-11-18T00:00:00Z', duration_seconds: 5, quality_score: 50 },
      { ...base, case_id: 'J3-89', evaluated_at: '2026-11-18T00:00:00Z', duration_seconds: null, quality_score: null },
    ];
    const page = await browser.newPage({ viewport: { width: 1200, height: 1100 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(data)));
    assert.equal(await page.locator('#case option').count(), 2);
    assert.equal(await page.locator('#revision option').count(), 2);
    assert.match(await page.locator('#coverage').innerText(), /3 次记录.*已评分 2 次/);
    assert.equal(await page.locator('#quality circle').count(), 2);
    assert.equal(await page.locator('#quality polyline').count(), 2); // Missing grade interrupts the curve.
    assert.equal(await page.locator('#latency circle').count(), 3);
    if (process.env.J3_HISTORY_SCREENSHOT) await page.screenshot({ path: process.env.J3_HISTORY_SCREENSHOT, fullPage: true });
    await page.locator('#revision').selectOption({ index: 1 });
    assert.match(await page.locator('#coverage').innerText(), /1 次记录.*已评分 1 次/);
    await page.locator('#case').selectOption('J3-89');
    assert.equal(await page.locator('#latency circle').count(), 0);
    assert.equal(await page.locator('#quality circle').count(), 0);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
