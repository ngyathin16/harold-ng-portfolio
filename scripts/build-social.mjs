import { chromium } from 'playwright';
import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const serif = await readFile(new URL('../node_modules/@fontsource-variable/newsreader/files/newsreader-latin-wght-normal.woff2', import.meta.url));
const sans = await readFile(new URL('../node_modules/@fontsource-variable/dm-sans/files/dm-sans-latin-wght-normal.woff2', import.meta.url));
const browser = await chromium.launch({
  ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : process.platform === 'win32' ? { channel: 'msedge' } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await page.setContent(`<!doctype html><html lang="en"><meta charset="utf-8"><style>
    @font-face{font-family:Newsreader;src:url(data:font/woff2;base64,${serif.toString('base64')});font-weight:200 800}
    @font-face{font-family:DM;src:url(data:font/woff2;base64,${sans.toString('base64')});font-weight:100 1000}
    *{box-sizing:border-box}body{margin:0;background:#f5f3ed;color:#252b29;padding:62px 76px;font-family:DM,sans-serif}.top{display:flex;justify-content:space-between;border-bottom:1px solid #d5d7cd;padding-bottom:28px;font-size:17px;color:#24594f}.mark{font:500 42px Newsreader;letter-spacing:-3px}.label{padding-top:13px}h1{font:400 150px/1 Newsreader;letter-spacing:-9px;margin:58px 0 15px}h1 span{color:#24594f}p{font:400 43px/1.2 Newsreader;margin:0}.bottom{display:flex;justify-content:space-between;align-items:center;margin-top:58px;font-size:16px;color:#626861}.line{width:110px;height:2px;background:#24594f}
    </style><div class="top"><span class="mark">hn.</span><span class="label">AI, and the software around it.</span></div><h1>Harold Ng<span>.</span></h1><p>AI engineer based in Hong Kong.</p><div class="bottom"><span>Selected work · Software · AI</span><span class="line"></span></div></html>`);
  await page.evaluate(() => document.fonts.ready);
  const output = new URL('../public/images/social-preview.png', import.meta.url);
  await mkdir(new URL('../public/images/', import.meta.url), { recursive: true });
  await page.screenshot({ path: fileURLToPath(output) });
  process.stdout.write(`Created ${fileURLToPath(output)}\n`);
} finally {
  await browser.close();
}
