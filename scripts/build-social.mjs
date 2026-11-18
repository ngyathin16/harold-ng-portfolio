import { chromium } from 'playwright';
import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const serif = await readFile(new URL('../node_modules/@fontsource/newsreader/files/newsreader-latin-400-normal.woff2', import.meta.url));
const sans = await readFile(new URL('../node_modules/@fontsource-variable/dm-sans/files/dm-sans-latin-wght-normal.woff2', import.meta.url));
const portrait = await readFile(new URL('../public/images/harold-portrait-480.webp', import.meta.url));
const browser = await chromium.launch({
  ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await page.setContent(`<!doctype html><html lang="en"><meta charset="utf-8"><title>Harold Ng — AI Solutions Engineer</title><style>
    @font-face{font-family:Newsreader;src:url(data:font/woff2;base64,${serif.toString('base64')});font-weight:400}
    @font-face{font-family:DM;src:url(data:font/woff2;base64,${sans.toString('base64')});font-weight:100 1000}
    *{box-sizing:border-box}
    body{margin:0;background:#f6f5f1;color:#202724;font-family:DM,sans-serif;width:1200px;height:630px;padding:42px 64px}
    .top{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #d9ded6;padding-bottom:22px}
    .mark{font:400 38px/1 Newsreader,serif;letter-spacing:-2px}.mark span{color:#24594f}
    .location{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#24594f;display:flex;align-items:center;gap:10px}
    .location::before{content:"";width:6px;height:6px;border-radius:50%;background:#24594f}
    main{display:grid;grid-template-columns:1fr 276px;gap:64px;align-items:center;margin-top:32px}
    .role{font-size:17px;line-height:1.4;letter-spacing:.08em;color:#24594f;margin:0 0 20px}
    h1{font:400 112px/.96 Newsreader,serif;letter-spacing:-5px;margin:0 0 24px}h1 span{color:#24594f}
    .intro{font-size:27px;line-height:1.4;max-width:580px;margin:0;color:#39483f}
    .tags{display:flex;gap:9px;margin-top:28px}.tags span{border:1px solid #d9ded6;border-radius:99px;padding:8px 13px;font-size:12px;letter-spacing:.01em;color:#4b5b51}
    figure{margin:0;position:relative;background:#172320;padding:8px;border-radius:16px;box-shadow:0 18px 38px #17232015}
    figure img{display:block;width:260px;height:346px;object-fit:cover;border-radius:10px}
    footer{display:flex;align-items:center;justify-content:space-between;margin-top:30px;padding-top:20px;border-top:1px solid #d9ded6;font-size:14px}
    .domain{font-weight:600;letter-spacing:.02em;color:#24594f}.work{color:#5c685f}.arrow{margin-left:12px}
    </style><body><header class="top"><span class="mark">hn<span>.</span></span><span class="location">Hong Kong</span></header>
    <main><section><p class="role">AI Solutions Engineer</p><h1>Harold Ng<span>.</span></h1><p class="intro">AI tools and the software that makes their decisions inspectable.</p><div class="tags"><span>AI systems</span><span>Data & evaluation</span><span>Software</span></div></section><figure><img src="data:image/webp;base64,${portrait.toString('base64')}" width="260" height="346" alt="Harold standing beneath a tree on a city street."></figure></main>
    <footer><span class="domain">haroldng.dev<span class="arrow">↗</span></span><span class="work">Selected work · Ideas, decisions, evidence</span></footer></body></html>`);
  await page.evaluate(() => document.fonts.ready);
  await page.locator('img').evaluate((element) => element.decode());
  assert.equal(await page.locator('.role').innerText(), 'AI Solutions Engineer');
  assert(await page.locator('footer').evaluate((element) => element.getBoundingClientRect().bottom <= innerHeight), 'Social preview content overflows its frame');
  const output = new URL('../public/images/social-preview.png', import.meta.url);
  await mkdir(new URL('../public/images/', import.meta.url), { recursive: true });
  await page.screenshot({ path: fileURLToPath(output) });
  process.stdout.write(`Created ${fileURLToPath(output)}\n`);
} finally {
  await browser.close();
}
