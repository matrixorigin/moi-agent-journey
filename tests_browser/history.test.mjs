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
    const production = await browser.newPage();
    production.on('pageerror', error => errors.push(error.message));
    await production.setContent(template.replace('/*RUN_DETAILS*/null', JSON.stringify({
      run_id: '1', attempt: 1, environment: 'Production', metrics_only: true,
    })));
    assert.equal(await production.title(), 'Journey-3 · Knowledge Base Tasks · Production');
    assert.equal(await production.locator('h1').innerText(), await production.title());
    await production.close();
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
    const details = {run_id: '3', attempt: 1, environment: 'QA', errors: [], scores: [],
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
    assert.equal(await page.title(), 'Journey-3 · Knowledge Base Tasks · QA');
    assert.equal(await page.locator('h1').innerText(), await page.title());
    assert.equal(await page.locator('#case').inputValue(), 'all');
    assert.equal(await page.locator('#report-coverage').innerText(), '本次记录 3 / 4 题 · 缺少 J3-58');
    assert.equal(await page.locator('#completed_seconds-thresholds').isVisible(), true);
    assert.equal(await page.locator('#completed_seconds').isVisible(), true);
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

test('threshold shares use metric-specific cohorts and include exact boundaries', async () => {
  const template = await readFile(new URL('../datasets/journey-3/history.html', import.meta.url), 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const rows = [
      ['1', 'J3-01', 'completed', 60, 10, 20, 1, 9],
      ['1', 'J3-02', 'completed', 120, 20, 40, 2, 8],
      ['1', 'J3-03', 'completed', 600, 40, 80, 2, 3],
      ['1', 'J3-04', 'completed', 601, 41, 81, 1, 0],
      ['1', 'J3-05', 'timeout', null, 2, null, 1, 4],
      ['1', 'J3-06', 'completed', null, null, null, 0, 0],
      ['2', 'J3-01', 'timeout', null, null, null, null, null],
      ['2', 'J3-02', 'completed', 90, null, null, null, null],
      ['3', 'J3-01', 'completed', 180, 30, 60, 0, 2],
    ].map(([run_id, case_id, qa_outcome, completed_seconds, completed_model_rounds, quality_score,
      tool_failed, tool_succeeded]) => ({
      run_id, case_id, qa_outcome, completed_seconds, completed_model_rounds, quality_score,
      tool_failed, tool_succeeded, run_attempt: 1, evaluated_at: '2026-11-19T00:00:00Z',
      answer_timing: run_id === '2' ? 'legacy' : 'final_answer_sse',
    }));
    const jobs = {documents: ['J3-01', 'J3-02'], operations: ['J3-03', 'J3-04', 'J3-05', 'J3-06']};
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(rows))
      .replace('/*HISTORY_JOBS*/{}', JSON.stringify(jobs)));
    const chart = id => page.locator(`#${id}-thresholds`);
    const points = (id, label) => chart(id).locator(`[data-series="${label}"] circle title`);
    const fixtures = [
      ['completed_seconds', 11, '≤120 秒', '2 / 4', '≤600 秒', '3 / 4', '≥600 秒', '2 / 4'],
      ['completed_model_rounds', 5, '≤20 轮', '2 / 4', '≤40 轮', '3 / 4', '≥40 轮', '2 / 4'],
      ['tool_failure_rate', 5, '≤20%', '3 / 6', '≤40%', '4 / 6', '≥40%', '2 / 6'],
      ['quality_score', 5, '≤40 分', '2 / 4', '≤80 分', '3 / 4', '≥80 分', '2 / 4'],
    ];
    for (const [id, series, low, lowCount, upper, upperCount, tail, tailCount] of fixtures) {
      assert.equal(await chart(id).locator('.chart-series').count(), series);
      assert.match(await points(id, low).first().textContent(), new RegExp(lowCount));
      assert.match(await points(id, upper).first().textContent(), new RegExp(upperCount));
      assert.match(await points(id, tail).first().textContent(), new RegExp(tailCount));
      assert.equal(await points(id, low).count(), 2);
      assert.equal(await chart(id).locator('polyline').count(), 0); // No samples for run 2.
    }
    assert.match(await points('tool_failure_rate', '≤20%').first().textContent(), /可计算 5 题/);
    assert.equal(await page.locator('#completed_seconds [data-case="J3-02"] circle').count(), 1);

    await page.locator('#case').selectOption('job:operations');
    assert.match(await points('completed_seconds', '≤600 秒').textContent(), /1 \/ 2 题（50\.0%）/);
    assert.match(await points('tool_failure_rate', '≤20%').textContent(), /1 \/ 4 题（25\.0%）/);
    const highScore = page.getByRole('button', {name: '≥80 分', exact: true});
    await highScore.click();
    assert.equal(await chart('quality_score').locator('.muted').count(), 4);
    assert.equal(await chart('tool_failure_rate').locator('.muted').count(), 0);
    await highScore.click();
    assert.equal(await chart('quality_score').locator('.muted').count(), 0);
    await page.locator('#case').selectOption('J3-03');
    assert.equal(await chart('completed_seconds').isHidden(), true);
    assert.equal(await page.locator('#completed_seconds').isVisible(), true);
  } finally {
    await browser.close();
  }
});
