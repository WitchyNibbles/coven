import assert from "node:assert/strict";
import { test } from "node:test";

import {
  configProblem,
  interfaceNames,
  onlyLoopback,
  parseConfig,
} from "./e2e/guards.ts";
import type { ProjectConfig } from "./e2e/guards.ts";

const HEADER =
  "Inter-|   Receive                                                |  Transmit\n" +
  " face |bytes    packets errs drop fifo frame compressed multicast|bytes\n";
const LO =
  "    lo:       0       0    0    0    0     0          0         0\n";
const ETH =
  "  eth0:     120       2    0    0    0     0          0         0\n";

const GOOD: ProjectConfig = {
  providers: { fake: { settings: { baseURL: "http://127.0.0.1:49999/v1" } } },
  policies: [
    { action: "provider.use", resource: "*", effect: "deny" },
    { action: "provider.use", resource: "fake", effect: "allow" },
  ],
};

await test("the two header lines are not interfaces", () => {
  assert.deepEqual(interfaceNames(HEADER + LO + ETH), ["lo", "eth0"]);
});

await test("only lo is accepted", () => {
  assert.equal(onlyLoopback(HEADER + LO), true);
});

await test("lo and eth0 are refused", () => {
  assert.equal(onlyLoopback(HEADER + LO + ETH), false);
});

await test("eth0 alone is refused", () => {
  assert.equal(onlyLoopback(HEADER + ETH), false);
});

await test("no interface at all is refused", () => {
  assert.equal(onlyLoopback(HEADER), false);
  assert.equal(onlyLoopback(""), false);
});

await test("a name that merely starts with lo is refused", () => {
  assert.equal(onlyLoopback(HEADER + "    lo0:       0\n"), false);
});

await test("the good config passes", () => {
  assert.equal(configProblem(GOOD), undefined);
});

await test("an extra provider is refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    providers: { ...GOOD.providers, other: {} },
  };
  assert.match(configProblem(config) ?? "", /only the fake provider/);
});

await test("no provider is refused", () => {
  assert.match(configProblem({ ...GOOD, providers: {} }) ?? "", /fake/);
  assert.match(configProblem({}) ?? "", /fake/);
});

await test("a base URL off loopback is refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    providers: { fake: { settings: { baseURL: "https://example.com/v1" } } },
  };
  assert.match(configProblem(config) ?? "", /127\.0\.0\.1/);
});

await test("a base URL that only starts like loopback is refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    providers: {
      fake: { settings: { baseURL: "http://127.0.0.1.evil.test/" } },
    },
  };
  assert.match(configProblem(config) ?? "", /127\.0\.0\.1/);
});

await test("a missing deny is refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    policies: [GOOD.policies?.[1] ?? {}],
  };
  assert.match(configProblem(config) ?? "", /deny/);
  assert.match(configProblem({ ...GOOD, policies: [] }) ?? "", /deny/);
});

await test("a deny of another action does not count", () => {
  const config: ProjectConfig = {
    ...GOOD,
    policies: [
      { action: "websearch.use", resource: "*", effect: "deny" },
      GOOD.policies?.[1] ?? {},
    ],
  };
  assert.match(configProblem(config) ?? "", /deny/);
});

await test("two allows are refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    policies: [
      ...(GOOD.policies ?? []),
      { action: "provider.use", resource: "other", effect: "allow" },
    ],
  };
  assert.match(configProblem(config) ?? "", /only the fake provider/);
});

await test("an allow of another provider instead of fake is refused", () => {
  const config: ProjectConfig = {
    ...GOOD,
    policies: [
      GOOD.policies?.[0] ?? {},
      { action: "provider.use", resource: "other", effect: "allow" },
    ],
  };
  assert.match(configProblem(config) ?? "", /only the fake provider/);
});

await test("parseConfig reads trailing commas", () => {
  assert.deepEqual(parseConfig('{"a": [1, 2,], "b": {"c": 3,},}'), {
    a: [1, 2],
    b: { c: 3 },
  });
});

await test("parseConfig leaves a comma-brace inside a string alone", () => {
  assert.deepEqual(parseConfig('{"a": "x,}", "b": "y, ]", "c": "q\\",}"}'), {
    a: "x,}",
    b: "y, ]",
    c: 'q",}',
  });
});
