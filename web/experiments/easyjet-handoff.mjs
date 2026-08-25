import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright-core";

const capturePath = process.argv[2];
if (!capturePath) throw new Error("booking-options capture path is required");
const capture = JSON.parse(await readFile(path.resolve(capturePath), "utf8"));
const option = capture.booking_options.find((item) => item.flight_numbers?.includes("U2 8309"));
const request = option?.booking_request;
if (!request?.url || !request?.post_data) throw new Error("U2 8309 handoff is incomplete");

const evidenceDir = path.resolve("experiments/evidence/easyjet-u2-8309");
await mkdir(evidenceDir, { recursive: true });
const networkEvidence = [];
const passengerKey = /adult|child|infant|teen|passenger|pax/i;

function collect(value, source, prefix = "$") {
  if (Array.isArray(value)) return value.forEach((item, index) => collect(item, source, `${prefix}[${index}]`));
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    const childPath = `${prefix}.${key}`;
    if (passengerKey.test(key) && ["string", "number", "boolean"].includes(typeof child)) {
      networkEvidence.push({ source, path: childPath, value: child });
    }
    if (typeof child === "object") collect(child, source, childPath);
  }
}

const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: false,
});
const context = await browser.newContext({ locale: "en-GB", timezoneId: "Europe/London" });
const page = await context.newPage();
page.on("request", (item) => {
  if (!/easyjet\.com/i.test(item.url()) || !["xhr", "fetch"].includes(item.resourceType())) return;
  const body = item.postData();
  if (!body) return;
  try { collect(JSON.parse(body), "NETWORK_REQUEST"); } catch {}
});
page.on("response", async (item) => {
  if (!/easyjet\.com/i.test(item.url()) || !["xhr", "fetch"].includes(item.request().resourceType())) return;
  try {
    const body = await item.text();
    if (body.length < 2_000_000) collect(JSON.parse(body), "NETWORK_RESPONSE");
  } catch {}
});

try {
  await page.setContent("<!doctype html><form id='handoff' method='post'></form>");
  await page.evaluate(({ url, postData }) => {
    const form = document.querySelector("#handoff");
    form.action = url;
    for (const [name, value] of new URLSearchParams(postData)) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.append(input);
    }
    form.submit();
  }, { url: request.url, postData: request.post_data });
  await page.waitForURL(/easyjet\.com/i, { timeout: 120_000 });
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(5_000);
  if ((await page.locator("body").innerText()).includes("Access Denied") && page.url().startsWith("http://www.easyjet.com/")) {
    await page.goto(page.url().replace(/^http:/, "https:"), { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(5_000);
  }
  await page.screenshot({ path: path.join(evidenceDir, "01-landing.png"), fullPage: true });

  const consent = page.getByRole("button", { name: /accept all|accept cookies|agree/i });
  if (await consent.first().isVisible().catch(() => false)) await consent.first().click();
  await page.waitForTimeout(3_000);
  const body = (await page.locator("body").innerText()).replace(/\s+/g, " ");
  const current = new URL(page.url());
  const adults = Number(current.searchParams.get("apax"));
  const children = Number(current.searchParams.get("cpax"));
  const flightVerified = /(?:U2|EZY)\s*8309/i.test(body) || current.searchParams.get("xdfn")?.includes("8309");
  const priceMatches = [...body.matchAll(/£\s*(\d+(?:[.,]\d{2})?)/g)].map((match) => Number(match[1].replace(",", ".")));
  const renderedPrice = priceMatches.find((price) => price >= 250 && price <= 450) ?? null;
  const fareSelected = /selected fare|fare selected/i.test(body);
  await page.screenshot({ path: path.join(evidenceDir, "02-deepest-state.png"), fullPage: true });

  const result = {
    carrier: "U2",
    flight_number: "U2 8309",
    route_verified: /Gatwick|LGW/i.test(body) && /Malpensa|MXP/i.test(body),
    date_verified: /18\s+Dec/i.test(body),
    departure_time_verified: body.includes("14:25"),
    arrival_time_verified: body.includes("17:25"),
    exact_flight_verified: Boolean(flightVerified),
    observed_adults: adults,
    observed_children: children,
    passenger_composition_verified: adults === 2 && children === 2,
    booking_option_price: Number(option.price),
    rendered_price: renderedPrice,
    fare_selected: fareSelected,
    network_evidence: networkEvidence.filter((item) => /count|adult|child|pax/i.test(item.path)),
  };
  await writeFile(path.join(evidenceDir, "result.json"), `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
