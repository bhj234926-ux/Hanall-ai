const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

(async () => {
  const outputDir = path.resolve("tmp", "ui");
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.HANALL_BROWSER_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
  await page.waitForSelector("#cameraBtn");
  await page.screenshot({ path: path.join(outputDir, "simulator-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector("#cameraBtn");
  await page.screenshot({ path: path.join(outputDir, "simulator-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("http://127.0.0.1:8765/admin/catalogs", { waitUntil: "networkidle" });
  await page.waitForSelector("#dropZone");
  await page.screenshot({ path: path.join(outputDir, "desktop.png"), fullPage: true });
  const pdfDir = path.join(outputDir, "pdfs");
  const pdfFiles = fs.readdirSync(pdfDir).filter((name) => name.endsWith(".pdf")).map((name) => path.join(pdfDir, name));
  await page.locator("#fileInput").setInputFiles(pdfFiles);
  await page.locator(".file-item").first().waitFor();
  const brandInputs = page.locator("input[data-field='brand']");
  for (let index = 0; index < await brandInputs.count(); index += 1) {
    await brandInputs.nth(index).fill("개나리벽지");
  }
  await page.locator("#startButton").click();
  await page.locator("#resultsView:not(.hidden)").waitFor({ timeout: 120000 });
  await page.screenshot({ path: path.join(outputDir, "results.png"), fullPage: true });
  await page.locator("#activateButton").click();
  await page.waitForFunction(() => document.querySelector("#activateButton")?.textContent.includes("완료"));
  await page.locator(".edit-button").first().click();
  await page.locator("#editModal:not(.hidden)").waitFor();
  await page.screenshot({ path: path.join(outputDir, "editor.png"), fullPage: true });
  await page.locator("[data-close-modal]").last().click();
  await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#catalogCount")?.textContent.trim() === "7");
  await page.screenshot({ path: path.join(outputDir, "simulator-linked.png"), fullPage: true });
  const title = await page.title();
  const uploadText = await page.locator("#cameraBtn").innerText();
  await browser.close();
  if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);
  console.log(`UI_OK title=${title} upload=${uploadText}`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
