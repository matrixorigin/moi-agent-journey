import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { chromium } from 'playwright';

test('J2 offline CLI report renders all views and filters cases without external requests', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'j2-score-'));
  const repo = fileURLToPath(new URL('..', import.meta.url));
  const browser = await chromium.launch({ headless: true });
  try {
    execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json,sys
from pathlib import Path
sys.path.insert(0,'tests')
from test_j2_score_runs import write_run
from journey.j2_scoring.__main__ import main
root=Path(sys.argv[1]);items=write_run(root/'old');write_run(root/'new',partial=True)
suite=root/'suite.json';suite.write_text(json.dumps({'suite_id':'browser-fixture','cases':items}))
main(['compare','--suite',str(suite),'--baseline-run',str(root/'old'),
      '--current-run',str(root/'new'),'--output',str(root/'report')])
main(['score','--suite',str(suite),'--current-run',str(root/'new'),'--output',str(root/'first')])
`, directory], { cwd: repo });
    const page = await browser.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
    await page.setContent(await readFile(join(directory, 'report/report.html'), 'utf8'));
    await page.locator('#visible-count').waitFor();
    assert.match(await page.locator('#visible-count').innerText(), /2 \/ 2/);
    assert.equal(await page.locator('#paired-count').innerText(), '2');
    for (const id of ['module-heatmap', 'score-chart', 'duration-chart', 'cumulative-chart']) {
      assert.equal(await page.locator('#' + id).isVisible(), true);
    }
    await page.locator('#domain-filter').selectOption('tools');
    assert.match(await page.locator('#visible-count').innerText(), /1 \/ 2/);
    assert.equal(await page.locator('.module-section:visible').count(), 1);
    await page.goto('about:blank');
    await page.setContent(await readFile(join(directory, 'first/report.html'), 'utf8'));
    assert.equal(await page.locator('#baseline-coverage').innerText(), '无基线');
    assert.match(await page.locator('h1').innerText(), /首轮基线/);
    assert.equal(await page.locator('#status-matrix').count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
  } finally {
    await browser.close();
    await rm(directory, { recursive: true, force: true });
  }
});
