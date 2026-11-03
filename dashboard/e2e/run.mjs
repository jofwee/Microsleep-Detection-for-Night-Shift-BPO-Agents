// End-to-end check of the dashboard against the HOSTED project, in a real browser (Edge, headless).
//
//   1. python scripts/dashboard_demo.py --out C:/Users/Mara/build/demo.json     (keeps running; creates a throwaway team)
//   2. npm run build            (in dashboard/)
//   3. node e2e/run.mjs         (this file; needs playwright-core in E2E_DEPS, default C:/Users/Mara/build/e2e)
//   4. create C:/Users/Mara/build/demo.stop to let the demo clean up
//
// It signs in as the demo team leader (temporary password first), visits every page, takes screenshots in light and
// dark, and performs the main actions (acknowledge an alert, create and deactivate an agent, start and stop a run).
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEPS = process.env.E2E_DEPS ?? "C:/Users/Mara/build/e2e";
const CREDS_FILE = process.env.E2E_CREDS ?? "C:/Users/Mara/build/demo.json";
const CREDS = JSON.parse(readFileSync(CREDS_FILE, "utf8"));
const SHOTS = process.env.E2E_SHOTS ?? "C:/Users/Mara/build/shots";
const ENVTXT = readFileSync(join(fileURLToPath(new URL("..", import.meta.url)), ".env"), "utf8");
const ENV = { url: /VITE_SUPABASE_URL=(.*)/.exec(ENVTXT)[1].trim(), key: /VITE_SUPABASE_PUBLISHABLE_KEY=(.*)/.exec(ENVTXT)[1].trim() };
const PORT = 4173;
const BASE = `http://localhost:${PORT}`;
mkdirSync(SHOTS, { recursive: true });

const { chromium } = await import(pathToFileURL(join(DEPS, "node_modules/playwright-core/index.mjs")).href);

const results = [];
const check = (name, ok, detail = "") => {
  results.push([name, !!ok]);
  console.log(`${ok ? "  ok    " : "  FAIL  "}${name}${!ok && detail ? `   [${detail}]` : ""}`);
};
const section = (t) => console.log(`\n== ${t}`);

const server = spawn("npx", ["vite", "preview", "--port", String(PORT), "--strictPort"], { cwd: fileURLToPath(new URL("..", import.meta.url)), shell: true, stdio: "ignore" });
await new Promise((r) => setTimeout(r, 4000));

const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const shot = async (name) => {
  await page.waitForTimeout(1300); // the sections fade in over about a second
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
};
const seen = async (text, timeout = 15000) => {
  try {
    await page.getByText(text, { exact: false }).first().waitFor({ timeout });
    return true;
  } catch {
    return false;
  }
};
const goto = async (path) => {
  await page.evaluate((p) => { window.history.pushState({}, "", p); window.dispatchEvent(new PopStateEvent("popstate")); }, path);
};

try {
  section("sign-in");
  await page.goto(`${BASE}/`);
  check("a signed-out visitor lands on the sign-in page", await seen("call desk") && page.url().endsWith("/login"));
  await shot("01-login-light");

  await page.getByLabel("email").fill(CREDS.leader_email);
  await page.getByLabel("password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "sign in" }).click();
  check("a wrong password is refused with a plain message", await seen("Wrong email or password"));

  if (CREDS.leader_password_new) {
    // a rerun: the temporary password was already replaced in an earlier run
    await page.getByLabel("password").fill(CREDS.leader_password_new);
    await page.getByRole("button", { name: "sign in" }).click();
    check("the leader's own password signs in", await seen("your team, right now"));
  } else {
    await page.getByLabel("password").fill(CREDS.leader_password);
    await page.getByRole("button", { name: "sign in" }).click();
    check("the temporary password leads to the new-password page", await seen("choose your own"));
    await shot("02-new-password");
    await page.getByLabel("new password").fill("short");
    await page.getByLabel("repeat it").fill("short");
    await page.getByRole("button", { name: "save password" }).click();
    check("a weak new password is refused", await seen("needs at least 12 characters"));
    const strong = "Dashboard-" + Math.random().toString(36).slice(2, 10) + "A1";
    await page.getByLabel("new password").fill(strong);
    await page.getByLabel("repeat it").fill(strong);
    await page.getByRole("button", { name: "save password" }).click();
    check("a good new password opens the dashboard", await seen("your team, right now"));
    writeFileSync(CREDS_FILE, JSON.stringify({ ...CREDS, leader_password_new: strong }, null, 1));
  }

  section("live");
  check("all three agents are listed", (await seen("Ana Reyes")) && (await seen("Ben Santos")) && (await seen("Carla Lim")));
  check("the agent on hold after a microsleep is shown as such", await seen("held: microsleep"));
  check("the microsleep uses the red chip", (await page.locator(".chip-alarm", { hasText: "microsleep" }).count()) > 0);
  check("the agent on a call is shown", await seen("on call"));
  check("the open identity-mismatch alert is listed", await seen("identity mismatch"));
  await page.waitForTimeout(800);
  await shot("03-live-light");

  section("live updates");
  await goto("/");
  await page.waitForTimeout(2500); // let the live subscription connect
  const openBefore = await page.getByRole("button", { name: "acknowledge" }).count();
  const ids = await page.evaluate(async (urlAndKey) => {
    const key = Object.keys(localStorage).find((k) => k.endsWith("-auth-token"));
    const token = JSON.parse(localStorage.getItem(key)).access_token;
    const headers = { apikey: urlAndKey.key, Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const open = await (await fetch(`${urlAndKey.url}/rest/v1/alerts?acknowledged_at=is.null&select=id&limit=1`, { headers })).json();
    if (!open.length) return "none-open";
    const r = await fetch(`${urlAndKey.url}/rest/v1/rpc/ack_alert`, { method: "POST", headers, body: JSON.stringify({ p_alert: open[0].id }) });
    return r.ok ? "acked" : `failed ${r.status}`;
  }, { url: ENV.url, key: ENV.key });
  let openAfter = openBefore;
  for (let i = 0; i < 16 && openAfter === openBefore; i++) {
    await page.waitForTimeout(500);
    openAfter = await page.getByRole("button", { name: "acknowledge" }).count();
  }
  check("an alert acknowledged elsewhere disappears from the open list without a reload", ids === "acked" && openAfter === openBefore - 1, `${ids} before=${openBefore} after=${openAfter}`);

  section("live transcript");
  await goto(`/calls/${CREDS.live_call}`);
  check("the live call page opens with its transcript so far", await seen("Hindi po gumagana yung internet"));
  await page.waitForTimeout(2500); // let the live subscription connect
  writeFileSync(CREDS_FILE.replace(/\.json$/, ".turn"), "go");
  check("a new line of the conversation appears without a reload", await seen("LIVE-TURN-MARKER", 20000));
  await goto("/");

  section("every page");
  const pages = [
    ["/queue", "answered in 20 s", "04-queue"],
    ["/calls", "call log", "05-calls"],
    ["/simulation", "start a run", "06-simulation"],
    ["/alerts", "waiting for you", "07-alerts"],
    ["/agents", "your team", "08-agents"],
    ["/reports", "fatigue through the day", "09-reports"],
    ["/team", "audit trail", "10-team"],
  ];
  for (const [path, text, name] of pages) {
    await goto(path);
    check(`${path} shows its content`, await seen(text));
    await page.waitForTimeout(900);
    await shot(`${name}-light`);
  }

  section("queue and reports show real numbers");
  await goto("/queue");
  check("the arrivals chart is drawn", await seen("Arrivals by hour"));
  check("the chart has bars", (await page.locator(".recharts-bar-rectangle").count()) > 0);
  await page.getByRole("button", { name: "view table" }).first().click();
  check("the table view of the chart shows the same numbers", await seen("completed", 5000));
  await goto("/reports");
  check("fatigue events are charted by hour", await seen("Events by hour of the day"));
  check("fatigue is related to the kind of call", await seen("Microsleeps per hour of talk"));

  section("calls and transcripts");
  await goto(`/calls/${CREDS.history_call}`);
  check("a call shows its transcript", await seen("Hindi po gumagana yung internet"));
  check("the per-turn timing is shown", await seen("speech to text"));
  await shot("11-call-detail-light");
  await goto("/calls");
  const rows = await page.locator("tbody tr").count();
  check("the call log lists the day's calls", rows >= 30, `rows=${rows}`);

  section("one agent");
  await goto(`/agents/${CREDS.agents[0].id}`);
  check("the agent page opens", await seen("Ana Reyes"));
  check("eye closure is charted", await seen("Eye closure (PERCLOS)"));
  check("the face-check history is listed", await seen("sign-in face checks"));
  await page.waitForTimeout(900);
  await shot("12-agent-light");

  section("acknowledging an alert");
  await goto("/alerts");
  const before = await page.getByRole("button", { name: "acknowledge" }).count();
  await page.getByRole("button", { name: "acknowledge" }).first().click();
  await page.waitForTimeout(1500);
  const after = await page.getByRole("button", { name: "acknowledge" }).count();
  check("acknowledging an alert removes it from the open list", before > 0 && after === before - 1, `before=${before} after=${after}`);

  section("team: create, deactivate");
  await goto("/team");
  await page.getByLabel("full name").fill("Dee Test");
  await page.getByLabel("email (their sign-in name)").fill(`dee-${Date.now()}@example.com`);
  await page.getByRole("button", { name: "create agent" }).click();
  check("creating an agent shows their temporary password once", await seen("temporary password") && await seen("shown only once"));
  await shot("13-team-secret");
  await page.getByRole("button", { name: "done" }).click();
  check("the new agent appears in the list", await seen("Dee Test"));
  const deeRow = page.locator("tr", { hasText: "Dee Test" });
  await deeRow.getByRole("button", { name: "deactivate" }).click();
  check("deactivating asks for confirmation", await seen("cannot sign in until you activate"));
  await page.keyboard.press("Escape");
  check("Escape closes the dialog", (await page.getByText("cannot sign in until you activate").count()) === 0);
  await deeRow.getByRole("button", { name: "deactivate" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "deactivate" }).click();
  await page.waitForTimeout(1500);
  check("the account is deactivated", (await deeRow.getByText("deactivated").count()) > 0);

  section("simulation: start and stop");
  await goto("/simulation");
  await page.getByLabel("calls per minute").fill("100");
  check("an out-of-range rate is explained before sending", await seen("must be between 0.5 and 30", 3000));
  await page.getByLabel("calls per minute").fill("2");
  await page.getByLabel(/seed/).fill("7");
  await page.getByRole("button", { name: "start simulation" }).click();
  check("a run starts", await seen("running", 15000) && await seen("stop simulation", 15000));
  await shot("14-simulation-running");
  await page.getByRole("button", { name: "stop simulation" }).click();
  check("the run stops and appears in the history", await seen("earlier runs") && (await seen("seed 7", 15000)));

  section("dark mode");
  await page.getByRole("button", { name: "dark", exact: true }).click();
  await page.waitForTimeout(800);
  check("dark mode swaps the colour tokens", await page.evaluate(() => document.documentElement.classList.contains("dark")));
  await shot("15-simulation-dark");
  for (const [path, name] of [["/", "16-live-dark"], ["/reports", "17-reports-dark"], ["/agents/" + CREDS.agents[0].id, "18-agent-dark"]]) {
    await goto(path);
    await page.waitForTimeout(1200);
    await shot(name);
  }
  await page.getByRole("button", { name: "light", exact: true }).click();

  section("phone width");
  await page.setViewportSize({ width: 390, height: 844 });
  await goto("/");
  await page.waitForTimeout(1000);
  await shot("19-live-phone");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("the live page has no sideways scroll on a phone", overflow <= 1, `overflow=${overflow}px`);

  section("sign out");
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "sign out" }).click();
  check("signing out returns to the sign-in page", await seen("team leaders") && page.url().endsWith("/login"));
  await goto("/team");
  await page.waitForTimeout(800);
  check("a signed-out visitor cannot reach another page", page.url().endsWith("/login"));

  section("browser console");
  const real = consoleErrors.filter((e) => !/favicon|Failed to load resource.*(400|401|403)/i.test(e));
  check("no errors were logged in the browser", real.length === 0, real.slice(0, 3).join(" | "));
} catch (e) {
  check("the run finished without an exception", false, String(e).slice(0, 300));
  console.log("browser errors:", consoleErrors.slice(0, 5).join(" | "));
  try { await shot("zz-failure"); } catch { /* ignore */ }
} finally {
  await browser.close();
  server.kill();
  spawn("taskkill", ["/pid", String(server.pid), "/t", "/f"], { shell: true, stdio: "ignore" });
  const failed = results.filter(([, ok]) => !ok).map(([n]) => n);
  console.log(`\n${results.length - failed.length} of ${results.length} checks passed`);
  for (const n of failed) console.log("  FAILED:", n);
  process.exitCode = failed.length ? 1 : 0;
}
