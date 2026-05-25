// Log in to the Presence app and screenshot the login page + dashboard.
// Run from a dir where `playwright-core` is installed (see SKILL.md):
//   cd /tmp/shotdir && node /path/to/.claude/skills/screenshot-app/shot.mjs
// Uses the system Google Chrome (channel: 'chrome') — no browser download.
import { chromium } from 'playwright-core';

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const USER = process.env.PRESENCE_USER || 'alan';
const PASS = process.env.PRESENCE_PASS || 'demopass1234';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();

// Login page (anonymous)
await page.goto(BASE + '/login/', { waitUntil: 'networkidle' });
await page.screenshot({ path: '/tmp/shot_login.png' });
console.log('login title:', await page.title());

// Authenticate, then the dashboard
await page.fill('#id_username', USER);
await page.fill('#id_password', PASS);
await Promise.all([
  page.waitForURL(BASE + '/', { waitUntil: 'networkidle' }),
  page.click('button[type=submit]'),
]);
await page.screenshot({ path: '/tmp/shot_index.png' });
console.log('after-login url:', page.url());

await browser.close();
console.log('wrote /tmp/shot_login.png and /tmp/shot_index.png');
