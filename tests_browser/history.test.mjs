import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { chromium } from 'playwright';

test('history overlays the selected job or all cases while keeping case and model series separate', async () => {
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
      evaluator_version: 'judge1', model_key: 'a'.repeat(32), qa_outcome: 'completed', run_attempt: 1,
      completed_model_rounds: 3, tool_failed: 0, tool_succeeded: 2 };
    const data = [
      { ...base, run_id: '1', evaluated_at: '2026-11-17T00:00:00Z', completed_seconds: 30, quality_score: 75 },
      { ...base, run_id: '2', evaluated_at: '2026-11-17T06:00:00Z', completed_seconds: 20, quality_score: null },
      { ...base, run_id: '3', evaluated_at: '2026-11-18T00:00:00Z', completed_seconds: 10, quality_score: 100 },
      { ...base, run_id: '4', model_key: 'b'.repeat(32), evaluated_at: '2026-11-19T00:00:00Z',
        completed_seconds: 5, quality_score: 50 },
      { ...base, run_id: '3', case_id: 'J3-89', evaluated_at: '2026-11-18T00:00:00Z', qa_outcome: 'timeout',
        completed_seconds: null, completed_model_rounds: null, quality_score: null, tool_failed: 2 },
      { ...base, run_id: '1', case_id: 'J3-57', evaluated_at: '2026-11-17T00:00:00Z',
        completed_seconds: 45, quality_score: 50 },
      { ...base, run_id: '3', case_id: 'J3-57', evaluated_at: '2026-11-18T00:00:00Z',
        completed_seconds: 35, quality_score: 75 },
      { ...base, run_id: '3', case_id: 'J3-58', evaluated_at: '2026-11-18T00:10:00Z',
        completed_seconds: 40, quality_score: 75 },
      { ...base, run_id: '3', run_attempt: 2, case_id: 'J3-57', evaluated_at: '2026-11-19T00:03:00Z',
        qa_outcome: 'excluded', completed_seconds: null, completed_model_rounds: null, quality_score: null },
    ];
    const jobs = { documents: ['J3-57'], operations: ['J3-58', 'J3-88', 'J3-89'] };
    const details = {run_id: '3', attempt: 1, errors: [], scores: [],
      coverage: {recorded: 3, planned: 4, missing: ['J3-58']},
      timeouts: [['J3-89', 'session-test', 'trace-test', 60]],
      tools: [['J3-89', 'session-test', 'trace-test', 'bash', 'test command', 'test error']]};
    const runMetadata = {
      1: {number: 93, attempt: 1, started_at: '2026-11-17T00:00:00Z'},
      2: {number: 94, attempt: 1, started_at: '2026-11-17T06:00:00Z'},
      3: {number: 95, attempt: 2, created_at: '2026-11-18T00:00:00Z', started_at: '2026-11-19T00:02:00Z'},
      4: {number: 96, attempt: 1, started_at: '2026-11-19T00:00:00Z'},
    };
    const page = await browser.newPage({ viewport: { width: 1200, height: 1100 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(data))
      .replace('/*HISTORY_JOBS*/{}', JSON.stringify(jobs))
      .replace('/*RUN_DETAILS*/null', JSON.stringify(details))
      .replace('/*RUN_METADATA*/{}', JSON.stringify(runMetadata)));
    const latency = 'completed_seconds';
    const quality = 'quality_score';
    const casesIn = async plot => page.locator('#' + plot + ' .chart-series').evaluateAll(nodes => nodes.map(node => node.dataset.case));
    assert.equal(await page.locator('#case').inputValue(), 'all');
    assert.equal(await page.locator('#report-coverage').innerText(), '本次记录 3 / 4 题 · 缺少 J3-58');
    const ticks = page.locator('#completed_seconds [data-run-tick]');
    assert.deepEqual(await ticks.evaluateAll(nodes => nodes.map(node => node.firstChild.nodeValue)),
      ['#93', '#94', '#95', '#95.2', '#96']);
    const positions = await ticks.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('x'))));
    assert.deepEqual(positions.slice(1).map((x, i) => x - positions[i]), [102.75, 102.75, 102.75, 102.75]);
    assert.match(await ticks.nth(3).locator('title').textContent(), /#95\.2.*2026\/11\/19 08:02:00.*北京时间/);
    assert.deepEqual(await casesIn(latency), ['J3-88', 'J3-88', 'J3-89', 'J3-57', 'J3-58']);
    assert.deepEqual(await casesIn(quality), await casesIn(latency));
    assert.equal(await page.locator('#revision').isVisible(), false);
    assert.equal(await page.locator('#completion_rate circle').count(), 1); // Incomplete runs leave gaps.
    assert.match(await page.locator('#completion_rate circle title').textContent(), /75$/);
    assert.match(await page.locator('#completion_rate circle title').textContent(), /#95.*2026\/11\/18 08:00:00/);
    assert.match(await page.locator('#timeout_rate circle title').textContent(), /25$/);
    const sharedDateX = await page.locator('#completed_seconds [data-case="J3-57"] circle').last().getAttribute('cx');
    assert.equal(await page.locator('#completed_seconds [data-case="J3-58"] circle').getAttribute('cx'), sharedDateX);

    await page.locator('#case').selectOption('job:documents');
    assert.deepEqual(await casesIn(latency), ['J3-57']);
    assert.equal(await page.locator('#quality_score circle').count(), 2);
    await page.locator('#case').selectOption('job:operations');
    assert.deepEqual(await casesIn(latency), ['J3-88', 'J3-88', 'J3-89', 'J3-58']);
    await page.locator('[data-detail=timeouts]').click();
    assert.match(await page.locator('#detail-records').innerText(), /J3-89.*session-test.*trace-test.*60 秒/s);
    await page.locator('[data-detail=tools]').click();
    await page.locator('#detail-records summary').click();
    assert.match(await page.locator('#detail-records article').innerText(), /test command.*test error/s);
    const legendEntry = page.getByRole('button', { name: 'J3-88', exact: true });
    await legendEntry.hover();
    assert.equal(await page.locator('#completed_seconds .chart-series.muted').count(), 2);
    assert.equal(await page.locator('#quality_score .chart-series.muted').count(), 2);
    await legendEntry.click();
    assert.equal(await page.locator('#revision').isVisible(), true);
    assert.equal(await page.locator('#revision option').count(), 2);
    assert.match(await page.locator('#coverage').innerText(), /3 次记录.*已评分 2 次/);
    assert.equal(await page.locator('#rates').isHidden(), true);
    assert.equal(await page.locator('#quality_score circle').count(), 2);
    assert.equal(await page.locator('#quality_score polyline').count(), 0); // Missing grade interrupts the curve.
    assert.equal(await page.locator('#completed_seconds circle').count(), 3);
    if (process.env.J3_HISTORY_SCREENSHOT) await page.screenshot({ path: process.env.J3_HISTORY_SCREENSHOT, fullPage: true });
    await page.locator('#revision').selectOption({ index: 1 });
    assert.match(await page.locator('#coverage').innerText(), /1 次记录.*已评分 1 次/);
    await page.locator('#case').selectOption('J3-89');
    assert.equal(await page.locator('#completed_seconds circle').count(), 0);
    assert.equal(await page.locator('#quality_score circle').count(), 0);
    assert.equal(await page.locator('#tool_failure_rate circle').count(), 1);
    assert.deepEqual(await casesIn(latency), ['J3-89']);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
