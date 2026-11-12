// Renders assets/make_icon.html to assets/icon.png (1024x1024, transparent corners).
//   node assets/make_icon.js && python assets/make_icon.py
const path = require('path');
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const page = await b.newPage({ viewport: { width: 1024, height: 1024 } });
  await page.goto('file://' + path.join(__dirname, 'make_icon.html'));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(__dirname, 'icon.png'), omitBackground: true });
  await b.close();
})();
