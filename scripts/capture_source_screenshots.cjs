const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_PACKAGE_PATH,
    path.join(process.cwd(), "node_modules", "playwright"),
    path.resolve(process.cwd(), "..", "juya-news-card", "node_modules", "playwright"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (_) {
      // Try the next local install location.
    }
  }

  return require("playwright");
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
}

async function main() {
  const [, , specPath, resultPath] = process.argv;
  if (!specPath || !resultPath) {
    throw new Error("Usage: node scripts/capture_source_screenshots.cjs <spec.json> <result.json>");
  }

  const spec = readJson(specPath);
  const outputDir = path.resolve(spec.outputDir);
  const timeoutMs = Number(spec.timeoutMs || 18000);
  fs.mkdirSync(outputDir, { recursive: true });

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    locale: "zh-CN",
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  });

  const results = [];
  try {
    for (const item of spec.items || []) {
      const fileName = String(item.fileName || `${item.index}.png`);
      const targetPath = path.join(outputDir, fileName);
      const page = await context.newPage();
      try {
        await page.route("**/*", (route) => {
          const type = route.request().resourceType();
          if (type === "font" || type === "media") {
            route.abort();
          } else {
            route.continue();
          }
        });
        await page.goto(item.url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
        await page.waitForTimeout(1200);
        await page.screenshot({
          path: targetPath,
          fullPage: false,
          animations: "disabled",
        });
        results.push({ index: item.index, fileName, ok: true });
      } catch (error) {
        results.push({
          index: item.index,
          fileName,
          ok: false,
          error: error && error.message ? error.message : String(error),
        });
      } finally {
        await page.close().catch(() => {});
      }
    }
  } finally {
    await browser.close().catch(() => {});
  }

  writeJson(resultPath, { items: results });
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
