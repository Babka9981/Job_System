const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright-core");
const AxeBuilder = require("@axe-core/playwright").default;

const root = path.resolve(__dirname, "../..");
const python = path.join(root, ".venv", "Scripts", "python.exe");

function createIsolation() {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "job-browser-acceptance-"));
  return { work, env: {
    ...process.env,
    JOB_DATABASE_PATH: path.join(work, "acceptance.sqlite3"),
    JOB_PRIVATE_ROOT: path.join(work, "private"),
    JOB_TEMPORARY_ROOT: path.join(work, "temporary"),
    JOB_MATCH_CACHE_ROOT: path.join(work, "private", "matching-cache"),
    JOB_ACCEPTANCE_META: path.join(work, "meta.json"),
    JOB_OWNER_USERNAME: "owner",
    DJANGO_DEBUG: "true",
  }};
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const reservation = net.createServer();
    reservation.unref();
    reservation.once("error", reject);
    reservation.listen(0, "127.0.0.1", () => {
      const { port } = reservation.address();
      resolve({ port, release: () => new Promise((done) => reservation.close(done)) });
    });
  });
}

function command(args, env) {
  const result = spawnSync(python, args, { cwd: root, env, encoding: "utf8" });
  if (result.status !== 0) throw new Error(`${args.join(" ")}\n${result.stdout}\n${result.stderr}`);
}

async function waitForServer(baseURL, server) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`Django acceptance server exited with ${server.exitCode}`);
    try {
      const response = await fetch(`${baseURL}/accounts/login/`);
      if (response.ok) return;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("Django acceptance server did not start");
}

async function stopProcessTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function startServer(env) {
  let lastError;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const reservation = await reservePort();
    const port = reservation.port;
    const baseURL = `http://127.0.0.1:${port}`;
    await reservation.release();
    const server = spawn(python, ["manage.py", "runserver", `127.0.0.1:${port}`, "--noreload"], {
      cwd: root, env, stdio: ["ignore", "pipe", "pipe"],
    });
    let serverLog = "";
    server.stdout.on("data", (chunk) => { serverLog += chunk; });
    server.stderr.on("data", (chunk) => { serverLog += chunk; });
    try {
      await waitForServer(baseURL, server);
      return { server, baseURL, port, readLog: () => serverLog };
    } catch (error) {
      lastError = error;
      await stopProcessTree(server);
    }
  }
  throw lastError || new Error("Could not allocate an acceptance server port");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function validateProtectedEvidence(evidence) {
  assert(evidence.status >= 200 && evidence.status < 300, `${evidence.route}: HTTP ${evidence.status}`);
  assert(evidence.finalURL === evidence.expectedURL, `${evidence.route}: redirected to ${evidence.finalURL}`);
  assert(evidence.hasShell, `${evidence.route}: protected app shell missing`);
  assert(evidence.hasMain, `${evidence.route}: protected main marker missing`);
  assert(evidence.username === "owner", `${evidence.route}: authenticated owner marker missing`);
  assert(!evidence.hasLoginForm, `${evidence.route}: login form rendered instead of protected page`);
  assert(evidence.heading === evidence.expectedHeading, `${evidence.route}: expected heading ${evidence.expectedHeading}, got ${evidence.heading}`);
}

async function assertProtectedPage(page, response, route, baseURL) {
  validateProtectedEvidence({
    route: route.path,
    status: response ? response.status() : 0,
    expectedURL: new URL(route.path, baseURL).href,
    finalURL: page.url(),
    hasShell: await page.locator("[data-shell]").count() === 1,
    hasMain: await page.locator("main#main-content").count() === 1,
    username: (await page.locator(".user-name").textContent() || "").trim(),
    hasLoginForm: await page.locator('form input[name="password"]').count() > 0,
    heading: (await page.locator("main#main-content h1").first().textContent() || "").trim(),
    expectedHeading: route.heading,
  });
}

async function login(page, baseURL) {
  await page.goto(`${baseURL}/accounts/login/`);
  await page.locator('input[name="username"]').fill("owner");
  await page.locator('input[name="password"]').fill("browser-acceptance-password");
  await Promise.all([
    page.waitForURL(`${baseURL}/`),
    page.getByRole("button", { name: "Войти" }).click(),
  ]);
}

async function assertPage(page, baseURL, route, viewport, theme, consoleProblems) {
  const response = await page.goto(`${baseURL}${route.path}`, { waitUntil: "networkidle" });
  await assertProtectedPage(page, response, route, baseURL);
  assert(await page.locator("html").getAttribute("data-theme") === theme, `${route.path}: theme ${theme} missing`);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert(overflow <= 1, `${route.path}: horizontal overflow ${overflow}px at ${viewport.width}`);
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const zoomOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert(zoomOverflow <= 1, `${route.path}: 200% text overflow ${zoomOverflow}px at ${viewport.width}`);
  await page.evaluate(() => { document.documentElement.style.fontSize = ""; });
  if (route.path === "/sources/") {
    const geometry = await page.locator(".sources-table-container").evaluate((container) => {
      container.scrollLeft = 200;
      container.scrollTop = 100;
      const table = container.querySelector(".sources-table");
      const firstColumn = container.querySelector("tbody th[scope='row']");
      const columnHeader = container.querySelector("thead th:nth-child(2)");
      const containerRect = container.getBoundingClientRect();
      const firstColumnRect = firstColumn.getBoundingClientRect();
      const columnHeaderRect = columnHeader.getBoundingClientRect();
      return {
        hasHorizontalOverflow: container.scrollWidth > container.clientWidth,
        hasVerticalOverflow: container.scrollHeight > container.clientHeight,
        scrolledHorizontally: container.scrollLeft > 0,
        scrolledVertically: container.scrollTop > 0,
        firstColumnPosition: getComputedStyle(firstColumn).position,
        headerPosition: getComputedStyle(columnHeader).position,
        firstColumnOffset: Math.abs(firstColumnRect.left - containerRect.left),
        headerOffset: Math.abs(columnHeaderRect.top - containerRect.top),
        tableWiderThanRegion: table.getBoundingClientRect().width > containerRect.width,
      };
    });
    assert(geometry.hasHorizontalOverflow && geometry.tableWiderThanRegion, `${route.path}: table does not own horizontal overflow at ${viewport.width}`);
    assert(geometry.hasVerticalOverflow, `${route.path}: table region does not own vertical overflow at ${viewport.width}`);
    assert(geometry.scrolledHorizontally && geometry.scrolledVertically, `${route.path}: table region is not locally scrollable at ${viewport.width}`);
    assert(geometry.firstColumnPosition === "sticky" && geometry.firstColumnOffset <= 1, `${route.path}: first column is not sticky at ${viewport.width}`);
    assert(geometry.headerPosition === "sticky" && geometry.headerOffset <= 1, `${route.path}: header is not sticky at ${viewport.width}`);
  }
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const severe = axe.violations.filter((item) => item.impact === "serious" || item.impact === "critical");
  assert(severe.length === 0, `${route.path}: axe ${severe.map((item) => item.id).join(",")}`);
  assert(consoleProblems.length === 0, `${route.path}: console ${consoleProblems.join(" | ")}`);
}

async function run() {
  const isolation = createIsolation();
  let serverInfo;
  let browser;
  try {
    command(["manage.py", "migrate", "--no-input"], isolation.env);
    command([path.join("tests", "acceptance", "browser_seed.py")], isolation.env);
    const { vacancy_id: vacancyId } = JSON.parse(fs.readFileSync(isolation.env.JOB_ACCEPTANCE_META, "utf8"));
    serverInfo = await startServer(isolation.env);
    const { baseURL } = serverInfo;
    const executablePath = process.env.PLAYWRIGHT_CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
    assert(fs.existsSync(executablePath), `Chrome executable missing: ${executablePath}`);
    browser = await chromium.launch({ executablePath, headless: true });
      const viewports = [
        { width: 1920, height: 1080 }, { width: 1280, height: 800 },
        { width: 992, height: 900 }, { width: 768, height: 1024 },
        { width: 390, height: 844 },
      ];
      const routes = [
        { path: "/", heading: "Вакансии" },
        { path: "/profile/", heading: "Мой профиль" },
        { path: "/sources/", heading: "Источники" },
        { path: `/vacancies/${vacancyId}/`, heading: "Product Manager" },
        { path: `/vacancies/${vacancyId}/drafts/`, heading: "Отклики · Product Manager" },
      ];
      let scans = 0;
      for (const theme of ["light", "dark"]) {
        for (const viewport of viewports) {
          const context = await browser.newContext({ viewport, permissions: ["clipboard-read", "clipboard-write"] });
          await context.addInitScript((value) => localStorage.setItem("theme", value), theme);
          const page = await context.newPage();
          const consoleProblems = [];
          page.on("console", (message) => {
            if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`);
          });
          page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));
          await login(page, baseURL);
          for (const route of routes) {
            await assertPage(page, baseURL, route, viewport, theme, consoleProblems);
            scans += 1;
          }
          if (viewport.width <= 992) {
            const route = routes[0];
            const response = await page.goto(`${baseURL}${route.path}`, { waitUntil: "networkidle" });
            await assertProtectedPage(page, response, route, baseURL);
            const toggle = page.getByRole("button", { name: "Открыть меню" });
            await toggle.focus();
            await toggle.press("Enter");
            assert(await toggle.getAttribute("aria-expanded") === "true", "drawer did not open from keyboard");
            await page.keyboard.press("Tab");
            assert(await page.locator("#primary-navigation").evaluate((drawer) => drawer.contains(document.activeElement)), "focus did not enter drawer");
            await page.keyboard.press("Escape");
            assert(await toggle.getAttribute("aria-expanded") === "false", "drawer did not close on Escape");
            assert(await toggle.evaluate((element) => element === document.activeElement), "focus did not return to menu toggle");
          }
          await context.close();
        }
      }

      const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write"] });
      const page = await context.newPage();
      await login(page, baseURL);
      const draftRoute = routes[4];
      const response = await page.goto(`${baseURL}${draftRoute.path}`, { waitUntil: "networkidle" });
      await assertProtectedPage(page, response, draftRoute, baseURL);
      const editor = page.locator("#cover_letter-text");
      await editor.fill("Edited through the browser DOM");
      const [saveResponse] = await Promise.all([
        page.waitForNavigation({ waitUntil: "networkidle" }),
        editor.locator("xpath=ancestor::form").getByRole("button", { name: "Сохранить" }).click(),
      ]);
      await assertProtectedPage(page, saveResponse, draftRoute, baseURL);
      assert(await editor.inputValue() === "Edited through the browser DOM", "browser edit was not persisted");
      await editor.locator("xpath=ancestor::form").getByRole("button", { name: "Копировать текст" }).click();
      assert(await page.evaluate(() => navigator.clipboard.readText()) === "Edited through the browser DOM", "clipboard does not contain editor text");
      assert(await editor.locator("xpath=ancestor::form").getByRole("button", { name: "Скопировано" }).isVisible(), "copy success was not rendered");
      await context.close();
      process.stdout.write(`browser_acceptance_ok scans=${scans} viewports=5 themes=2 axe_serious_critical=0 console=clean copy=ok port=${serverInfo.port}\n`);
  } catch (error) {
    const serverLog = serverInfo ? serverInfo.readLog() : "server not started";
    process.stderr.write(`${error.stack}\nserver log:\n${serverLog}\n`);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (serverInfo) await stopProcessTree(serverInfo.server);
    fs.rmSync(isolation.work, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

module.exports = { createIsolation, reservePort, validateProtectedEvidence };
if (require.main === module) run();
