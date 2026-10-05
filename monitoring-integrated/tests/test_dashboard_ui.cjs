// Browser UI verification with mocked API/Grafana responses, not a live Grafana test.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const html = fs.readFileSync(path.join(__dirname, '../runtime/agent/index.html'), 'utf8');
    const home = '/grafana/d/integrated-multilog?from=now-15m&to=now&refresh=1m';
    let releaseSearch;
    let deferSearch = false;
    await page.route('http://sentinel.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname === '/api/config') return route.fulfill({ json: { engine: 'keyword', default_url: home } });
      if (url.pathname === '/api/search') {
        if (deferSearch) await new Promise(resolve => { releaseSearch = resolve; });
        return route.fulfill({ json: {
          dashboard_url: '/grafana/d/integrated-multilog?from=now-60m&to=now&var-status=500&var-status=501&var-log_type=alb',
          plan: { minutes: 60, server: '', ip: '', path: '', status: '5xx', event: 'all', log_type: 'alb', action: 'all' },
          summary: '검색 결과', query: 'mock query', rows: []
        }}).catch(() => {});
      }
      return route.fulfill({ contentType: 'text/html', body: '<html><body>Grafana placeholder (mock)</body></html>' });
    });
    await page.goto('http://sentinel.test/');
    await page.waitForFunction(() => document.querySelectorAll('.panel-card iframe').length === 5);
    const src = async id => new URL(await page.locator('#panel-' + id).getAttribute('src'), 'http://sentinel.test');
    assert.equal((await src(5)).pathname, '/grafana/d-solo/integrated-multilog');
    assert.equal((await src(5)).searchParams.get('panelId'), '5');
    await page.locator('#zoom-in-5').click();
    assert.equal((await src(5)).searchParams.get('from'), 'now-8m');
    assert.equal((await src(2)).searchParams.get('from'), 'now-15m');
    await page.locator('#zoom-reset-5').click();
    assert.equal((await src(5)).searchParams.get('from'), 'now-15m');
    for (let i = 0; i < 4; i++) await page.locator('#zoom-in-5').click();
    assert.equal(await page.locator('#zoom-in-5').isDisabled(), true);
    for (let i = 0; i < 11; i++) await page.locator('#zoom-out-5').click();
    assert.equal(await page.locator('#zoom-out-5').isDisabled(), true);
    assert.equal((await src(5)).searchParams.get('from'), 'now-1440m');
    await page.locator('#question').fill('ALB 5xx');
    await page.locator('#submit').click();
    await page.waitForFunction(() => document.querySelector('#applied').textContent.includes('60분'));
    await page.locator('#zoom-in-5').click();
    assert.equal((await src(5)).searchParams.get('from'), 'now-30m');
    assert.deepEqual((await src(5)).searchParams.getAll('var-status'), ['500', '501']);
    assert.equal((await src(6)).searchParams.get('from'), 'now-60m');
    await page.locator('#home').click();
    for (const id of [2, 3, 4, 5, 6]) {
      assert.equal((await src(id)).searchParams.get('from'), 'now-15m');
      assert.equal((await src(id)).searchParams.has('var-status'), false);
    }
    assert.equal(await page.locator('#question').inputValue(), '');
    assert.equal(await page.locator('#evidence').isVisible(), false);
    deferSearch = true;
    await page.locator('#question').fill('slow search');
    await page.locator('#submit').click();
    await page.waitForFunction(() => document.querySelector('#submit').disabled);
    await page.locator('#home').click();
    if (releaseSearch) releaseSearch();
    assert.equal((await src(5)).searchParams.get('from'), 'now-15m');
    assert.equal(await page.locator('#submit').isEnabled(), true);
    await page.locator('#home').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#question').inputValue(), '');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS: independent zoom, bounds, filter preservation, home reset, pending search reset, keyboard, mobile overflow');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
