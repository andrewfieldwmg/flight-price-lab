import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright-core";
import { canPrepareBooking } from "./booking-handoff-safety.mjs";

const capturePath = process.argv[2];
if (!capturePath) {
  throw new Error("Usage: node experiments/verify-ryanair-passengers.mjs <booking-options.json>");
}

const expected = { adults: 2, children: 2, total: 4 };
const capture = JSON.parse(await readFile(path.resolve(capturePath), "utf8"));
const option = capture.booking_options?.find((candidate) =>
  candidate.flight_numbers?.includes("FR 2687"),
);
const bookingRequest = option?.booking_request;
if (!bookingRequest?.url || !bookingRequest?.post_data) {
  throw new Error("Saved capture has no FR 2687 booking_request");
}

const passengerKey = /adult|child|teen|infant|passenger|travell?er|pax/i;
const evidence = [];

function collectPassengerFields(value, source, prefix = "$") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectPassengerFields(item, source, `${prefix}[${index}]`));
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    const childPath = `${prefix}.${key}`;
    if (passengerKey.test(key) && ["string", "number", "boolean"].includes(typeof child)) {
      evidence.push({ source, path: childPath, value: child });
    }
    if (typeof child === "object") collectPassengerFields(child, source, childPath);
  }
}

function inspectEncodedBody(body, source) {
  if (!body) return;
  try {
    collectPassengerFields(JSON.parse(body), source);
    return;
  } catch {}
  const params = new URLSearchParams(body);
  for (const [key, value] of params) {
    if (passengerKey.test(key) && value.length < 100) {
      evidence.push({ source, path: `form.${key}`, value });
    }
  }
}

const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: false,
});
const context = await browser.newContext({ locale: "en-GB", timezoneId: "Europe/London" });
const page = await context.newPage();

page.on("request", (request) => {
  if (/ryanair\.com/i.test(request.url()) && ["xhr", "fetch"].includes(request.resourceType())) {
    inspectEncodedBody(request.postData(), "NETWORK_REQUEST");
  }
});
page.on("response", async (response) => {
  if (!/ryanair\.com/i.test(response.url())) return;
  const type = response.request().resourceType();
  if (!["xhr", "fetch"].includes(type)) return;
  const contentType = response.headers()["content-type"] ?? "";
  if (!/json|text/i.test(contentType)) return;
  try {
    const body = await response.text();
    if (body.length <= 2_000_000) inspectEncodedBody(body, "NETWORK_RESPONSE");
  } catch {}
});

try {
  await page.setContent("<!doctype html><form id='handoff' method='post'></form>");
  await page.evaluate(
    ({ url, postData }) => {
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
    },
    { url: bookingRequest.url, postData: bookingRequest.post_data },
  );
  await page.waitForURL(/ryanair\.com/i, { timeout: 120_000 });

  const consent = page.getByRole("button", {
    name: /accept all|accept cookies|yes,? i agree|no,? thanks/i,
  });
  if (await consent.first().isVisible().catch(() => false)) await consent.first().click();
  await page.getByText("Your selected flight", { exact: true }).waitFor({ timeout: 30_000 });

  const editSearch = page.getByRole("button", { name: /edit search/i });
  if (await editSearch.isVisible().catch(() => false)) {
    await editSearch.click();
    await page.waitForTimeout(1_000);
  }
  const passengerControl = page.getByRole("button", { name: /guest|passenger|travell?er|4\s*$/i });
  if (await passengerControl.first().isVisible().catch(() => false)) {
    await passengerControl.first().click();
    await page.waitForTimeout(500);
  }

  const domText = (await page.locator("body").innerText()).replace(/\s+/g, " ");
  const adultMatch = domText.match(/Adults?[^0-9]{0,40}(\d+)/i);
  const childMatch = domText.match(/Children?[^0-9]{0,40}(\d+)/i);
  if (adultMatch) evidence.push({ source: "DOM", path: "adult_count", value: Number(adultMatch[1]) });
  if (childMatch) evidence.push({ source: "DOM", path: "child_count", value: Number(childMatch[1]) });

  const storage = await page.evaluate(() => ({
    local: Object.fromEntries(Object.entries(localStorage)),
    session: Object.fromEntries(Object.entries(sessionStorage)),
    jsonScripts: [...document.querySelectorAll("script[type='application/json']")].map(
      (script) => script.textContent,
    ),
  }));
  for (const [area, entries] of [["LOCAL_STORAGE", storage.local], ["SESSION_STORAGE", storage.session]]) {
    for (const [key, value] of Object.entries(entries)) {
      if (passengerKey.test(key) && value.length < 100) evidence.push({ source: area, path: key, value });
      try { collectPassengerFields(JSON.parse(value), area, key); } catch {}
    }
  }
  for (const script of storage.jsonScripts) {
    try { collectPassengerFields(JSON.parse(script), "APP_STATE"); } catch {}
  }

  const observedAdults = evidence.find((item) =>
    /adult/i.test(item.path) && Number(item.value) === expected.adults,
  )?.value ?? null;
  const observedChildren = evidence.find((item) =>
    /child/i.test(item.path) && Number(item.value) === expected.children,
  )?.value ?? null;
  const browserVerified = Number(observedAdults) === 2 && Number(observedChildren) === 2;
  const handoffParameters = capture.search_parameters ?? {};
  const handoffVerified = Number(handoffParameters.adults) === 2 && Number(handoffParameters.children) === 2;
  const exactFlightVerified = /Your selected flight/i.test(domText) && /FR\s*2687/i.test(domText);
  const passengerCompositionVerified = browserVerified && handoffVerified;
  const result = {
    expected_adults: 2,
    expected_children: 2,
    observed_adults: observedAdults === null ? null : Number(observedAdults),
    observed_children: observedChildren === null ? null : Number(observedChildren),
    evidence_source: [...new Set(evidence.map((item) => item.source))],
    browser_verified: browserVerified,
    handoff_payload_verified: handoffVerified,
    independently_verified: passengerCompositionVerified,
    confidence: passengerCompositionVerified ? "HIGH" : handoffVerified ? "MEDIUM" : "LOW",
    safety_gate_passed: canPrepareBooking({
      exactFlightVerified,
      passengerCompositionVerified,
    }),
    automation_proceeded: false,
    evidence,
  };
  await writeFile(
    path.resolve("experiments/evidence/ryanair-fr2687/passenger-composition.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
