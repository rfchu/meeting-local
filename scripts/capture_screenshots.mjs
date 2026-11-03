// Standalone CDP script for capturing screenshots in Headless Chrome
const wsUrl = process.env.WS_URL;
const SCREENSHOTS_DIR = process.env.SCREENSHOTS_DIR;
const PORT = process.env.PORT || 8768;
const aidV1 = process.env.AID_V1;

function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  const ws = new globalThis.WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });

  let msgId = 1;
  const pending = new Map();
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      pending.get(data.id)(data);
      pending.delete(data.id);
    }
  };

  function send(method, params = {}) {
    return new Promise((resolve) => {
      const id = msgId++;
      pending.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async function evaluate(code) {
    const res = await send("Runtime.evaluate", {
      expression: code,
      returnByValue: true,
      awaitPromise: true,
    });
    return res.result?.result?.value;
  }

  async function takeScreenshot(fileName) {
    await wait(1200);
    const res = await send("Page.captureScreenshot", { format: "png" });
    const fs = await import("node:fs");
    const path = await import("node:path");
    const filePath = path.join(SCREENSHOTS_DIR, fileName);
    fs.writeFileSync(filePath, Buffer.from(res.result.data, "base64"));
    console.log(`Saved screenshot: ${fileName} (${fs.statSync(filePath).size} bytes)`);
  }

  await send("Page.enable");
  await send("DOM.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 920,
    deviceScaleFactor: 2,
    mobile: false,
  });

  // Navigate to home
  await send("Page.navigate", { url: `http://127.0.0.1:${PORT}` });
  await wait(2200);

  // 1. Recording Workbench
  console.log("1/5: 01-recording-workbench.png");
  await evaluate(`document.querySelector('.record-button')?.click()`);
  await wait(1500);
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('开始录音'))?.click()`);
  await wait(1500);
  await evaluate(`(() => {
    const input = document.querySelector('textarea[placeholder*="重点"]');
    if (input) {
      input.value = '关于星河项目交付：上线时间调整为下周一 10:00，核心支付压测通过';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  })()`);
  await wait(800);
  await takeScreenshot("01-recording-workbench.png");

  // Close workbench
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('结束') || b.textContent.includes('停止'))?.click()`);
  await wait(800);
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('暂不处理') || b.textContent.includes('关闭'))?.click()`);
  await wait(1000);

  // Re-navigate to home
  await send("Page.navigate", { url: `http://127.0.0.1:${PORT}` });
  await wait(2000);

  // Open meeting
  await evaluate(`Array.from(document.querySelectorAll('.meeting-item')).find(el => el.textContent.includes('星河项目交付评审会'))?.click()`);
  await wait(1500);

  // 2. Side-by-side Review
  console.log("2/5: 02-side-by-side-review.png");
  await evaluate(`Array.from(document.querySelectorAll('.tabs button')).find(b => b.textContent.includes('会后整理'))?.click()`);
  await wait(1500);
  await evaluate(`window.scrollTo({ top: 160, behavior: 'instant' })`);
  await wait(800);
  await takeScreenshot("02-side-by-side-review.png");

  // 3. Meeting Outcomes & Delivery Freeze
  console.log("3/5: 03-meeting-outcomes.png");
  await evaluate(`Array.from(document.querySelectorAll('.tabs button')).find(b => b.textContent.includes('会议成果'))?.click()`);
  await wait(1200);
  // Expand delivery details and hide transcript pane, then scroll to minutes document
  await evaluate(`(() => {
    const details = document.querySelector('.minutes-delivery details');
    if (details) details.open = true;
    const transcriptHideBtn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('收起原文'));
    if (transcriptHideBtn) transcriptHideBtn.click();
    const doc = document.querySelector('.minutes-document');
    if (doc) doc.scrollIntoView({ behavior: 'instant', block: 'start' });
    else window.scrollTo({ top: 320, behavior: 'instant' });
  })()`);
  await wait(1500);
  await takeScreenshot("03-meeting-outcomes.png");

  // 4. Side-by-side Minutes Diff
  console.log("4/5: 04-minutes-diff.png");
  // Scroll back to top
  await evaluate(`window.scrollTo({ top: 0, behavior: 'instant' })`);
  await wait(500);
  // Still on "会议成果" tab, open version options details
  await evaluate(`(() => {
    const details = document.querySelector('details.minutes-version-options');
    if (details) details.open = true;
  })()`);
  await wait(1000);
  // Select v1 in dropdown
  await evaluate(`(() => {
    const select = document.querySelector('select[aria-label="纪要版本"]');
    if (select) {
      select.value = "${aidV1}";
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  })()`);
  await wait(1500);
  // Click "与采用稿对比"
  await evaluate(`(() => {
    const compareBtn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('与采用稿对比') || b.textContent.includes('对比'));
    if (compareBtn) compareBtn.click();
  })()`);
  await wait(1800);
  // Click "只看差异"
  await evaluate(`document.querySelector('#minutesCompareDialog input[type="checkbox"]')?.click()`);
  await wait(1500);
  await takeScreenshot("04-minutes-diff.png");

  // Close compare dialog
  await evaluate(`Array.from(document.querySelectorAll('#minutesCompareDialog button')).find(b => b.textContent.includes('关闭'))?.click()`);
  await wait(1000);

  // 5. Cross-Meeting Citation QA
  console.log("5/5: 05-cross-meeting-qa.png");
  await evaluate(`Array.from(document.querySelectorAll('aside button')).find(b => b.textContent.includes('搜索与引用问答'))?.click()`);
  await wait(1500);
  await evaluate(`Array.from(document.querySelectorAll('.search-dialog .tabs button')).find(b => b.textContent.includes('引用问答'))?.click()`);
  await wait(1500);
  // Open history details and click the item
  await evaluate(`(() => {
    const details = document.querySelector('details.qa-history');
    if (details) details.open = true;
    const historyItem = Array.from(document.querySelectorAll('.qa-history button')).find(b => b.textContent.includes('星河项目'));
    if (historyItem) historyItem.click();
  })()`);
  await wait(2200);
  // Scroll modal so answer and citations are nicely visible
  await evaluate(`(() => {
    const qaAnswer = document.querySelector('.qa-answer');
    if (qaAnswer) qaAnswer.scrollIntoView({ behavior: 'instant', block: 'center' });
  })()`);
  await wait(1000);
  await takeScreenshot("05-cross-meeting-qa.png");

  console.log("All screenshots done successfully!");
  ws.close();
  process.exit(0);
}

run().catch(err => { console.error(err); process.exit(1); });
