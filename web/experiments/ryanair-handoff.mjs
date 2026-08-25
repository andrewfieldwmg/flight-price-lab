import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright-core";

const EXPECTED = {
  carrier: "FR",
  flightNumber: "2687",
  route: ["STN", "CAG"],
  date: "2026-12-18",
  adults: 2,
  children: 2,
  discoveryPrice: 741,
  bookingOptionPrice: 813,
  currency: "GBP",
};

const capturePath = process.argv[2];
if (!capturePath) {
  throw new Error("Usage: node experiments/ryanair-handoff.mjs <booking-options.json>");
}

const capture = JSON.parse(await readFile(path.resolve(capturePath), "utf8"));
const option = capture.booking_options?.find(
  (candidate) =>
    candidate.book_with === "Ryanair" &&
    candidate.flight_numbers?.includes("FR 2687"),
);
const request = option?.booking_request;
if (!request?.url || !request?.post_data) {
  throw new Error("Saved capture has no complete Ryanair booking_request");
}

const evidenceDir = path.resolve("experiments/evidence/ryanair-fr2687");
await mkdir(evidenceDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: false,
});
const context = await browser.newContext({ locale: "en-GB", timezoneId: "Europe/London" });
const page = await context.newPage();

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
    { url: request.url, postData: request.post_data },
  );

  await page.waitForURL(/ryanair\.com/i, { timeout: 120_000 });
  await page.waitForLoadState("domcontentloaded");
  await page.screenshot({
    path: path.join(evidenceDir, "01-initial-ryanair-landing.png"),
    fullPage: true,
  });

  const consent = page.getByRole("button", {
    name: /accept all|accept cookies|yes,? i agree|no,? thanks/i,
  });
  if (await consent.first().isVisible().catch(() => false)) {
    await consent.first().click();
  }
  await page.getByText("Your selected flight", { exact: true }).waitFor({ timeout: 30_000 });
  const basicFareAction = page.getByRole("button", { name: /continue with basic/i });
  await basicFareAction.waitFor({ timeout: 30_000 });

  const bodyText = await page.locator("body").innerText();
  const normalized = bodyText.replace(/\s+/g, " ");
  const flightVerified = /Your selected flight/i.test(normalized) && /FR\s*2687/i.test(normalized);
  const routeVerified = /London Stansted/i.test(normalized) && /Cagliari/i.test(normalized);
  const dateVerified = /18\s+Dec/i.test(normalized);
  const adultsVerified = /2\s+adult/i.test(normalized);
  const childrenVerified = /2\s+child/i.test(normalized);
  const passengerCountVerified = adultsVerified && childrenVerified;
  const selectedAutomatically = flightVerified;
  const fareSelected = false;
  const basicFareText = await basicFareAction.innerText();
  const renderedPriceDigits = basicFareText.match(/£[\s\S]*?(\d[\d\s.,]*\d)/)?.[1]
    .replace(/\s/g, "")
    .replace(",", ".");
  const renderedPrice = renderedPriceDigits ? Number(renderedPriceDigits) : null;

  await page.screenshot({
    path: path.join(evidenceDir, "02-flight-selection-state.png"),
    fullPage: true,
  });

  const verificationTimestamp = new Date().toISOString();
  const result = {
    carrier: EXPECTED.carrier,
    flight_number: `FR ${EXPECTED.flightNumber}`,
    expected_price: EXPECTED.discoveryPrice,
    booking_option_price: EXPECTED.bookingOptionPrice,
    rendered_price: renderedPrice,
    currency: EXPECTED.currency,
    exact_flight_verified: flightVerified,
    passengers_verified: passengerCountVerified,
    fare_selected: fareSelected,
    handoff_stage: flightVerified ? "SELECTED_FLIGHT" : "FLIGHT_SELECTION",
    verification_timestamp: verificationTimestamp,
    verification: {
      carrier: flightVerified,
      flight: flightVerified,
      route: routeVerified,
      date: dateVerified,
      adults: adultsVerified,
      children: childrenVerified,
      selected_automatically: selectedAutomatically,
    },
    price_validation: {
      discovery_to_booking_delta: EXPECTED.bookingOptionPrice - EXPECTED.discoveryPrice,
      booking_to_ryanair_delta:
        renderedPrice === null ? null : renderedPrice - EXPECTED.bookingOptionPrice,
      total_delta: renderedPrice === null ? null : renderedPrice - EXPECTED.discoveryPrice,
      price_verified_at: verificationTimestamp,
    },
  };
  await writeFile(
    path.join(evidenceDir, "booking-handoff-result.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
