import assert from "node:assert/strict";
import test from "node:test";

import { isTenantResponseCurrent } from "../src/tenant-request-guard.js";


test("a delayed response from the previous company cannot update current state", async () => {
  let activeTenant = "company-a";
  let rendered = null;

  const delayedResponse = new Promise((resolve) => {
    setTimeout(() => resolve({ tenant: "company-a", value: "stale" }), 10);
  });
  activeTenant = "company-b";
  const currentResponse = { tenant: "company-b", value: "current" };

  if (isTenantResponseCurrent(activeTenant, currentResponse.tenant)) rendered = currentResponse.value;
  const staleResponse = await delayedResponse;
  if (isTenantResponseCurrent(activeTenant, staleResponse.tenant)) rendered = staleResponse.value;

  assert.equal(rendered, "current");
});

test("empty or mismatched company contexts are never accepted", () => {
  assert.equal(isTenantResponseCurrent("company-a", "company-b"), false);
  assert.equal(isTenantResponseCurrent("company-a", ""), false);
  assert.equal(isTenantResponseCurrent(null, null), false);
});
