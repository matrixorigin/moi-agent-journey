import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { chromium } from 'playwright';

test('Pages lists the four latest-run cohorts by case for the selected scope', async () => {
  const template = await readFile(new URL('../datasets/journey-3/history.html', import.meta.url), 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const base = {run_id: '42', run_attempt: 2, evaluated_at: '2026-11-21T00:00:00Z', duration_seconds: 240,
      completed_model_rounds: null, tool_failed: 0, tool_succeeded: 0, quality_score: null};
    const rows = [
      {...base, case_id: 'J3-01', qa_outcome: 'error'},
      {...base, case_id: 'J3-58', qa_outcome: 'timeout', tool_failed: 2, tool_succeeded: 6},
      {...base, case_id: 'J3-59', qa_outcome: 'completed', quality_score: 49},
      {...base, case_id: 'J3-60', qa_outcome: 'completed', quality_score: 50},
      {...base, case_id: 'J3-61', qa_outcome: 'completed', quality_score: 20, run_attempt: 1},
    ];
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(rows))
      .replace('/*HISTORY_JOBS*/{}', JSON.stringify({documents: ['J3-01'], operations: ['J3-58','J3-59','J3-60','J3-61']}))
      .replace('/*RUN_DETAILS*/null', JSON.stringify({run_id: '42', attempt: 2, environment: 'QA', metrics_only: true}))
      .replace('/*RUN_METADATA*/{}', JSON.stringify({'42': {number: 102}}))
      .replace('/*PAGES_ENVIRONMENT*/null', '"QA"'));
    assert.equal(await page.locator('#run-overview').isHidden(), true);
    assert.match(await page.locator('#details h2').innerText(), /最近一次运行明细 · #102\.2/);
    const records = page.locator('#detail-records tbody td');
    assert.deepEqual(await records.allTextContents(), ['J3-01']);
    await page.locator('[data-detail=timeouts]').click();
    assert.deepEqual(await records.allTextContents(), ['J3-58', '240 秒']);
    await page.locator('[data-detail=tools]').click();
    assert.deepEqual(await records.allTextContents(), ['J3-58', '2 次', '25%']);
    await page.locator('[data-detail=scores]').click();
    assert.deepEqual(await records.allTextContents(), ['J3-59', '49 分']);
    await page.locator('#case').selectOption('job:documents');
    assert.match(await page.locator('#detail-records').innerText(), /无符合条件/);
  } finally {
    await browser.close();
  }
});

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
    const publicRow = {case_id: 'J3-01', run_id: '1', run_attempt: 1, qa_outcome: 'completed',
      evaluated_at: '2026-11-17T00:00:00Z', quality_score: 25, duration_seconds: 10, tool_failed: 2, tool_succeeded: 1};
    await production.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify([
      publicRow, {...publicRow, run_attempt: 2, tool_failed: 15},
    ])).replace('/*RUN_DETAILS*/null', JSON.stringify({
      run_id: '1', attempt: 1, environment: 'Production', metrics_only: true, report_error_cases: 1,
      locations: {errors: [], timeouts: [], tools: [], scores: [['J3-01', 'score-session', 'score-trace']]},
    })));
    assert.equal(await production.title(), 'Journey-3 · Knowledge Base Tasks · Production');
    assert.equal(await production.locator('h1').innerText(), await production.title());
    assert.equal(await production.locator('#run-overview').isVisible(), true);
    await production.locator('[data-detail=scores]').click();
    assert.deepEqual(await production.locator('#detail-records th').allTextContents(), ['Case', 'Session ID', 'Trace ID', '得分']);
    assert.deepEqual(await production.locator('#detail-records td').allTextContents(), ['J3-01', 'score-session', 'score-trace', '25 分']);
    assert.deepEqual(await production.locator('#run-overview dd').allTextContents(),
      ['1 题', '1 / 1', '0 题', '0 题', '2 次', '1 / 1', '1 / 1', '1 题']);
    await production.close();
    const base = { case_id: 'J3-88', case_revision: 'r1', snapshot_id: 's1',
      evaluator_version: 'judge1', model_key: 'a'.repeat(32), qa_outcome: 'completed', run_attempt: 1,
      completed_model_rounds: 3, tool_failed: 0, tool_succeeded: 2 };
    const data = [
      { ...base, run_id: '1', evaluated_at: '2026-11-17T00:00:00Z', duration_seconds: 30, quality_score: 75 },
      { ...base, run_id: '2', evaluated_at: '2026-11-17T06:00:00Z', duration_seconds: 20, quality_score: null },
      { ...base, run_id: '3', evaluated_at: '2026-11-18T00:00:00Z', duration_seconds: 10, quality_score: 100 },
      { ...base, run_id: '4', model_key: 'b'.repeat(32), evaluated_at: '2026-11-19T00:00:00Z',
        duration_seconds: 5, quality_score: 50 },
      { ...base, run_id: '3', case_id: 'J3-89', evaluated_at: '2026-11-18T00:00:00Z', qa_outcome: 'timeout',
        duration_seconds: null, completed_model_rounds: null, quality_score: null, tool_failed: 2 },
      { ...base, run_id: '1', case_id: 'J3-57', evaluated_at: '2026-11-17T00:00:00Z',
        duration_seconds: 45, quality_score: 50 },
      { ...base, run_id: '3', case_id: 'J3-57', evaluated_at: '2026-11-18T00:00:00Z',
        duration_seconds: 35, quality_score: 75 },
      { ...base, run_id: '3', case_id: 'J3-58', evaluated_at: '2026-11-18T00:10:00Z',
        duration_seconds: 40, quality_score: 75 },
      { ...base, run_id: '3', run_attempt: 2, case_id: 'J3-57', evaluated_at: '2026-11-19T00:03:00Z',
        qa_outcome: 'excluded', duration_seconds: null, completed_model_rounds: null, quality_score: null },
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
    const latency = 'duration_seconds';
    const quality = 'quality_score';
    const casesIn = async plot => page.locator('#' + plot + ' .chart-series').evaluateAll(nodes => nodes.map(node => node.dataset.case));
    assert.equal(await page.title(), 'Journey-3 · Knowledge Base Tasks · QA');
    assert.equal(await page.locator('h1').innerText(), await page.title());
    assert.equal(await page.locator('#case').inputValue(), 'all');
    assert.equal(await page.locator('#report-coverage').innerText(), '本次记录 3 / 4 题 · 缺少 J3-58');
    assert.equal(await page.locator('#duration_seconds-thresholds').isVisible(), true);
    assert.equal(await page.locator('#duration_seconds').isVisible(), true);
    const ticks = page.locator('#duration_seconds [data-run-tick]');
    assert.deepEqual(await ticks.evaluateAll(nodes => nodes.map(node => node.firstChild.nodeValue)),
      ['#93', '#94', '#95', '#95.2', '#96']);
    const positions = await ticks.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('x'))));
    assert.deepEqual(positions.slice(1).map((x, i) => x - positions[i]), [102.75, 102.75, 102.75, 102.75]);
    assert.match(await ticks.nth(3).locator('title').textContent(), /#95\.2.*2026\/11\/19 08:02:00.*北京时间/);
    assert.deepEqual(await casesIn(latency), ['J3-88', 'J3-88', 'J3-89', 'J3-57', 'J3-58']);
    assert.deepEqual(await casesIn(quality), await casesIn(latency));
    assert.equal(await page.locator('#revision').isVisible(), false);
    assert.equal(await page.locator('#rate-coverage').isVisible(), true);
    for (const [chart, full, partial] of [
      ['completion_rate', '正常完成 3 / 4 题（75%）', '正常完成 2 / 2 题（100%）'],
      ['timeout_rate', '超时 1 / 4 题（25%）', '超时 0 / 2 题（0%）'],
    ]) {
      const points = page.locator('#' + chart + ' circle');
      const incomplete = points.and(page.locator('.partial-point'));
      const fullRun = points.and(page.locator(':not(.partial-point)'));
      assert.equal(await points.count(), 4); // The excluded-only rerun has no observed outcome.
      assert.equal(await incomplete.count(), 3);
      assert.equal(await page.locator('#' + chart + ' polyline').count(), 0);
      const segments = page.locator('#' + chart + ' .partial-segment');
      const endpoints = await segments.evaluateAll(nodes => nodes.map(node => [node.x1.baseVal.value, node.x2.baseVal.value])
        .sort((a, b) => a[0] - b[0]));
      assert.deepEqual(endpoints, [[positions[0], positions[1]], [positions[1], positions[2]]]);
      // The excluded-only rerun also prevents a link to the last point.
      const fullTooltip = await fullRun.locator('title').textContent();
      assert.match(fullTooltip, /#95.*2026\/11\/18 08:00:00/);
      assert.ok(fullTooltip.endsWith(full + ' · 已记录 4 / 4 题'));
      const partialTooltip = await incomplete.first().locator('title').textContent();
      assert.match(partialTooltip, /#93/);
      assert.ok(partialTooltip.endsWith(partial + ' · 已记录 2 / 4 题'));
    }
    const sharedDateX = await page.locator('#duration_seconds [data-case="J3-57"] circle').last().getAttribute('cx');
    assert.equal(await page.locator('#duration_seconds [data-case="J3-58"] circle').getAttribute('cx'), sharedDateX);

    await page.locator('#case').selectOption('job:documents');
    assert.deepEqual(await casesIn(latency), ['J3-57']);
    const documentGap = page.locator('#duration_seconds [data-case="J3-57"] .gap-segment');
    assert.deepEqual(await documentGap.evaluate(node => [node.x1.baseVal.value, node.x2.baseVal.value]),
      [positions[0], positions[2]]);
    assert.equal(await page.locator('#case-gaps').isVisible(), true);
    assert.equal(await page.locator('#quality_score circle').count(), 2);
    assert.equal(await page.locator('#completion_rate circle').count(), 2);
    assert.equal(await page.locator('#completion_rate .partial-point').count(), 0);
    assert.equal(await page.locator('#rate-coverage').isHidden(), true);
    await page.locator('#case').selectOption('job:operations');
    assert.deepEqual(await casesIn(latency), ['J3-88', 'J3-88', 'J3-89', 'J3-58']);
    await page.locator('[data-detail=timeouts]').click();
    assert.match(await page.locator('#detail-records').innerText(), /J3-89.*session-test.*trace-test.*60 秒/s);
    await page.locator('[data-detail=tools]').click();
    await page.locator('#detail-records summary').click();
    assert.match(await page.locator('#detail-records article').innerText(), /test command.*test error/s);
    const legendEntry = page.getByRole('button', { name: 'J3-88', exact: true });
    await legendEntry.hover();
    assert.equal(await page.locator('#duration_seconds .chart-series.muted').count(), 2);
    assert.equal(await page.locator('#quality_score .chart-series.muted').count(), 2);
    await legendEntry.click();
    assert.equal(await page.locator('#revision').isVisible(), true);
    assert.equal(await page.locator('#revision option').count(), 2);
    assert.match(await page.locator('#coverage').innerText(), /3 次记录.*已评分 2 次/);
    assert.equal(await page.locator('#rates').isHidden(), true);
    assert.equal(await page.locator('#rate-coverage').isHidden(), true);
    assert.equal(await page.locator('#quality_score circle').count(), 2);
    assert.equal(await page.locator('#quality_score polyline').count(), 0);
    const missingGrade = page.locator('#quality_score .gap-segment');
    assert.deepEqual(await missingGrade.evaluate(node => [node.x1.baseVal.value, node.x2.baseVal.value]),
      [positions[0], positions[2]]);
    assert.match(await missingGrade.locator('title').textContent(), /跨过 1 次/);
    assert.equal(await page.locator('#duration_seconds circle').count(), 3);
    if (process.env.J3_HISTORY_SCREENSHOT) await page.screenshot({ path: process.env.J3_HISTORY_SCREENSHOT, fullPage: true });
    await page.locator('#revision').selectOption({ index: 1 });
    assert.match(await page.locator('#coverage').innerText(), /1 次记录.*已评分 1 次/);
    assert.equal(await page.locator('#case-gaps').isHidden(), true);
    await page.locator('#case').selectOption('J3-89');
    assert.equal(await page.locator('#duration_seconds circle').count(), 0);
    assert.equal(await page.locator('#quality_score circle').count(), 0);
    assert.equal(await page.locator('#tool_failure_rate circle').count(), 1);
    assert.deepEqual(await casesIn(latency), ['J3-89']);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('the single-case score chart opens on a version with a recorded score', async () => {
  const template = await readFile(new URL('../datasets/journey-3/history.html', import.meta.url), 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const base = {case_id: 'J3-01', run_attempt: 1, evaluated_at: '2026-11-21T00:00:00Z',
      qa_outcome: 'completed', duration_seconds: 30, completed_model_rounds: 3,
      tool_failed: 0, tool_succeeded: 1};
    const rows = [
      {...base, run_id: '1', evaluator_version: 'correctness-criteria-v1', quality_score: null},
      {...base, run_id: '2', evaluator_version: 'correctness-facts-v2', quality_score: 0},
    ];
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(rows))
      .replace('/*RUN_DETAILS*/null', JSON.stringify({run_id: '2', attempt: 1, errors: [], timeouts: [], tools: [],
        scores: [['J3-01', 'latest-session', 'latest-trace', 0, ['missing fact']]],
      })));
    await page.locator('#case').selectOption('J3-01');
    assert.match(await page.locator('#revision').inputValue(), /correctness-facts-v2/);
    assert.equal(await page.locator('#quality_score circle').count(), 1);
    await page.locator('#revision').selectOption({index: 0});
    assert.equal(await page.locator('#quality_score circle').count(), 0);
    await page.locator('[data-detail=scores]').click();
    assert.match(await page.locator('#detail-records').innerText(), /latest-session[\s\S]*latest-trace/);
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
    ].map(([run_id, case_id, qa_outcome, duration_seconds, completed_model_rounds, quality_score,
      tool_failed, tool_succeeded]) => ({
      run_id, case_id, qa_outcome, duration_seconds, completed_model_rounds, quality_score,
      tool_failed, tool_succeeded, run_attempt: 1, evaluated_at: '2026-11-19T00:00:00Z',
    }));
    const jobs = {documents: ['J3-01', 'J3-02'], operations: ['J3-03', 'J3-04', 'J3-05', 'J3-06']};
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(rows))
      .replace('/*HISTORY_JOBS*/{}', JSON.stringify(jobs)));
    const chart = id => page.locator(`#${id}-thresholds`);
    const points = (id, label) => chart(id).locator(`[data-series="${label}"] circle title`);
    const fixtures = [
      ['duration_seconds', 11, '≤120 秒', '2 / 4', '≤600 秒', '3 / 4', '≥600 秒', '2 / 4'],
      ['completed_model_rounds', 5, '≤20 轮', '2 / 4', '≤40 轮', '3 / 4', '≥40 轮', '2 / 4'],
      ['tool_failure_rate', 5, '≤20%', '3 / 6', '≤40%', '4 / 6', '≥40%', '2 / 6'],
    ];
    for (const [id, series, low, lowCount, upper, upperCount, tail, tailCount] of fixtures) {
      assert.equal(await chart(id).locator('.chart-series').count(), series);
      assert.match(await points(id, low).first().textContent(), new RegExp(lowCount));
      assert.match(await points(id, upper).first().textContent(), new RegExp(upperCount));
      assert.match(await points(id, tail).first().textContent(), new RegExp(tailCount));
      const hasRecordedLegacySample = id === 'duration_seconds';
      assert.equal(await points(id, low).count(), hasRecordedLegacySample ? 3 : 2);
      assert.equal(await chart(id).locator('polyline').count(), hasRecordedLegacySample ? series : 0);
    }
    assert.match(await points('tool_failure_rate', '≤20%').first().textContent(), /可计算 5 题/);
    assert.equal(await page.locator('#duration_seconds [data-case="J3-02"] circle').count(), 2);
    assert.match(await page.locator('#duration_seconds [data-case="J3-02"] circle title').last().textContent(),
      / · 90\n/);
    assert.match(await page.locator('#duration_seconds [data-case="J3-01"] circle title').last().textContent(),
      / · 180\n/);
    assert.equal(await page.getByLabel('耗时口径').count(), 0);
    assert.equal(await page.getByRole('heading', {name: '正常完成任务耗时 · 秒', exact: true}).count(), 1);

    await page.locator('#case').selectOption('job:operations');
    assert.match(await points('duration_seconds', '≤600 秒').textContent(), /1 \/ 2 题（50\.0%）/);
    assert.match(await points('tool_failure_rate', '≤20%').textContent(), /1 \/ 4 题（25\.0%）/);
    await page.locator('#case').selectOption('J3-03');
    assert.equal(await chart('duration_seconds').isHidden(), true);
    assert.equal(await page.locator('#duration_seconds').isVisible(), true);
  } finally {
    await browser.close();
  }
});

test('score trends use the specified bands and plot the mean of the same completed scores', async () => {
  const template = await readFile(new URL('../datasets/journey-3/history.html', import.meta.url), 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const base = {run_id: '1', run_attempt: 1, evaluated_at: '2026-11-19T00:00:00Z',
      qa_outcome: 'completed', duration_seconds: null, completed_model_rounds: null, tool_failed: 0, tool_succeeded: 0};
    const scores = [0, 20, 49, 50, 79, 80, 89, 90, 99, 100, null];
    const rows = scores.map((quality_score, index) => ({
      ...base, case_id: 'J3-' + String(index + 1).padStart(2, '0'), quality_score,
    }));
    rows.push(
      {...base, case_id: 'J3-12', qa_outcome: 'timeout', quality_score: 100},
      {...base, case_id: 'J3-01', run_id: '2', quality_score: null},
      {...base, case_id: 'J3-01', run_id: '3', quality_score: 0},
    );
    await page.setContent(template.replace('/*HISTORY_DATA*/[]', JSON.stringify(rows))
      .replace('/*HISTORY_JOBS*/{}', JSON.stringify({documents: ['J3-01', 'J3-02']})));
    const chart = page.locator('#quality_score-thresholds');
    const points = label => chart.locator('[data-series="' + label + '"] circle title');
    const expected = [['0 分', 1], ['[0,50)', 3], ['[50,80)', 2], ['[80,90)', 2], ['[90,100]', 3], ['100 分', 1]];
    assert.equal(await chart.locator('.chart-series').count(), expected.length + 1);
    for (const [label, count] of expected) {
      assert.equal(await points(label).count(), 2);
      assert.ok((await points(label).first().textContent()).includes(count + ' / 10 题'));
    }
    assert.match(await points('平均分').first().textContent(), /65\.6 分 · 10 题/);
    assert.match(await points('平均分').last().textContent(), /平均分 0 分 · 1 题/);
    assert.equal(await chart.getByText('100 分', {exact: true}).count(), 1);

    await page.locator('#case').selectOption('job:documents');
    assert.match(await points('平均分').first().textContent(), /10 分 · 2 题/);
    const average = page.getByRole('button', {name: '平均分', exact: true});
    await average.click();
    assert.equal(await chart.locator('.muted').count(), 6);
    assert.equal(await page.locator('#tool_failure_rate-thresholds .muted').count(), 0);
    await average.click();
    assert.equal(await chart.locator('.muted').count(), 0);
  } finally {
    await browser.close();
  }
});
