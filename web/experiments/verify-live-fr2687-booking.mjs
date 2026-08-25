import process from "node:process";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8011";
const optionId = process.argv[3];
if (!optionId) throw new Error("FR 2687 option ID is required");

const apiBodies = [];
const consoleMessages = [];
const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: false,
});
const context = await browser.newContext({ locale: "en-GB", timezoneId: "Europe/London" });
const page = await context.newPage();
page.on("console", (message) => consoleMessages.push(message.text()));
page.on("response", async (response) => {
  if (!response.url().includes("/api/booking/")) return;
  try { apiBodies.push(await response.text()); } catch {}
});

try {
  await page.goto(`${baseUrl}/api/health`);
  await page.setContent("<button id='prepare'>Prepare booking</button><pre id='result'></pre>");
  await page.evaluate(({ baseUrl, optionId }) => {
    document.querySelector("#prepare").addEventListener("click", async () => {
      const response = await fetch(`${baseUrl}/api/booking/prepare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          search_id: "fr2687-live-check",
          selected_option_ids: [optionId],
        }),
      });
      document.querySelector("#result").textContent = JSON.stringify(await response.json());
    });
  }, { baseUrl, optionId });
  await page.getByRole("button", { name: "Prepare booking" }).click();
  await page.waitForFunction(() => document.querySelector("#result").textContent.length > 10);
  const session = JSON.parse(await page.locator("#result").textContent());
  const ticket = session.tickets[0];
  const acknowledgementAppears = ticket.material_change_acknowledgement_required;

  await page.evaluate(({ baseUrl, sessionId, ticketId, acknowledge }) => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = `${baseUrl}/api/booking/${sessionId}/handoff/${ticketId}${acknowledge ? "?acknowledge_material_change=true" : ""}`;
    document.body.append(form);
    form.submit();
  }, {
    baseUrl,
    sessionId: session.booking_session_id,
    ticketId: ticket.ticket_id,
    acknowledge: acknowledgementAppears,
  });
  await page.waitForURL(/ryanair\.com/i, { timeout: 120_000 });
  const consent = page.getByRole("button", { name: /yes,? i agree|no,? thanks/i });
  if (await consent.first().isVisible().catch(() => false)) await consent.first().click();
  await page.getByText("Your selected flight", { exact: true }).waitFor({ timeout: 30_000 });
  const edit = page.getByRole("button", { name: /edit search/i });
  if (await edit.isVisible().catch(() => false)) await edit.click();
  await page.waitForTimeout(500);
  const body = (await page.locator("body").innerText()).replace(/\s+/g, " ");
  const adult = Number(body.match(/Adults?[^0-9]{0,40}(\d+)/i)?.[1] ?? NaN);
  const child = Number(body.match(/Children?[^0-9]{0,40}(\d+)/i)?.[1] ?? NaN);
  const priceText = await page.getByRole("button", { name: /continue with basic/i }).innerText();
  const digits = priceText.match(/£[\s\S]*?(\d[\d\s.,]*\d)/)?.[1].replace(/\s/g, "").replace(",", ".");
  const renderedPrice = digits ? Number(digits) : null;
  const leakPattern = /booking_post_data|post_data|opaque-provider-post|\bu=[A-Za-z0-9_-]{100}/i;
  const result = {
    exact_flight_preserved: /FR\s*2687/i.test(body),
    route_preserved: /London Stansted/i.test(body) && /Cagliari/i.test(body),
    date_preserved: /18\s+Dec/i.test(body),
    adults: adult,
    children: child,
    booking_option_price: Number(ticket.current_price),
    rendered_price: renderedPrice,
    price_delta: Number(ticket.price_delta),
    price_change_status: ticket.price_change_status,
    acknowledgement_appears: acknowledgementAppears,
    session_state: session.state,
    fare_continuation_clicked: false,
    provider_post_in_api_responses: apiBodies.some((body) => leakPattern.test(body)),
    provider_post_in_frontend_state: leakPattern.test(JSON.stringify(session)),
    provider_post_in_console: consoleMessages.some((message) => leakPattern.test(message)),
  };
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
