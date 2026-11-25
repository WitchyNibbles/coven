import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { PermissionRule } from "../src/context.ts";
import { DENIED_COMMANDS, pmRules } from "../src/pm/rules.ts";

const rule = (
  action: string,
  resource: string,
  effect: PermissionRule["effect"],
): PermissionRule => ({ action, resource, effect });

/** The options plugin's `secretRules`: the secret rules, then the skills edit deny. */
const SECRET: readonly PermissionRule[] = [
  rule("read", "*.env", "deny"),
  rule("read", "*.env.*", "deny"),
  rule("read", "*.env.example", "allow"),
  rule("edit", "*.env", "deny"),
  rule("edit", "*.env.*", "deny"),
  rule("edit", "/placeholder/config/skills/*", "deny"),
];

const BASE = {
  store: "/placeholder/store",
  storeResolved: "/placeholder/real-store",
  tmp: "/placeholder/profile/tmp",
  secretRules: SECRET,
  extraRepos: ["/placeholder/other-repo"],
};

function matches(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped.replace(/\*/g, ".*")}$`, "s").test(value);
}

/** What the permission layer decides: the last rule that matches wins; no match is `none`. */
function evaluate(
  rules: readonly PermissionRule[],
  action: string,
  resource: string,
): string {
  const hit = [...rules]
    .reverse()
    .find((each) => each.action === action && matches(each.resource, resource));
  return hit?.effect ?? "none";
}

await test("pmRules holds the spec's list in its order", () => {
  assert.deepEqual(pmRules(BASE), [
    rule("shell", "*", "allow"),
    rule("edit", "*", "allow"),
    rule("read", "*", "allow"),
    rule("external_directory", "/placeholder/profile/tmp/*", "allow"),
    rule("external_directory", "/placeholder/other-repo/*", "allow"),
    rule("shell", "git push --force*", "deny"),
    rule("shell", "git push -f*", "deny"),
    rule("shell", "git push --force-with-lease*", "deny"),
    rule("shell", "git push origin main*", "deny"),
    rule("shell", "git push origin master*", "deny"),
    rule("shell", "gh pr merge*", "deny"),
    rule("edit", "/placeholder/store/*", "deny"),
    rule("edit", "/placeholder/real-store/*", "deny"),
    rule("webfetch", "*", "deny"),
    rule("question", "*", "deny"),
    ...SECRET,
  ]);
});

await test("pmRules denies force pushes, main pushes, gh pr merge, store edits by both paths, webfetch, question", () => {
  const rules = pmRules(BASE);
  for (const command of [
    "git push --force origin topic",
    "git push -f origin topic",
    "git push --force-with-lease origin topic",
    "git push origin main",
    "git push origin master",
    "gh pr merge 12 --squash",
  ]) {
    assert.equal(evaluate(rules, "shell", command), "deny", command);
  }
  assert.equal(evaluate(rules, "shell", "git push origin topic"), "allow");
  assert.equal(evaluate(rules, "shell", "git status"), "allow");
  assert.equal(evaluate(rules, "edit", "/placeholder/store/a.md"), "deny");
  assert.equal(evaluate(rules, "edit", "/placeholder/real-store/a.md"), "deny");
  assert.equal(evaluate(rules, "edit", "/placeholder/work/a.ts"), "allow");
  assert.equal(evaluate(rules, "webfetch", "https://example.test"), "deny");
  assert.equal(evaluate(rules, "question", "which one"), "deny");
});

await test("a store with no resolved twin or none at all adds only what exists", () => {
  const same = pmRules({ ...BASE, storeResolved: BASE.store });
  assert.equal(
    same.filter((each) => each.resource === "/placeholder/store/*").length,
    1,
  );
  const none = pmRules({ ...BASE, store: null, storeResolved: null });
  assert.equal(
    none.filter((each) => each.action === "edit" && each.effect === "deny")
      .length,
    SECRET.filter((each) => each.action === "edit" && each.effect === "deny")
      .length,
  );
});

await test("the secret denies come after the allows", () => {
  const rules = pmRules(BASE);
  const lastAllow = rules.reduce(
    (last, each, index) =>
      each.resource === "*" && each.effect === "allow" ? index : last,
    -1,
  );
  const firstSecret = rules.findIndex((each) => each === SECRET[0]);
  assert.ok(firstSecret > lastAllow);
  assert.equal(evaluate(rules, "read", "/placeholder/work/.env"), "deny");
  assert.equal(
    evaluate(rules, "read", "/placeholder/work/a.env.local"),
    "deny",
  );
  assert.equal(
    evaluate(rules, "read", "/placeholder/work/.env.example"),
    "allow",
  );
  assert.equal(evaluate(rules, "edit", "/placeholder/work/.env"), "deny");
});

await test("the skills edit deny is last", () => {
  const rules = pmRules(BASE);
  assert.deepEqual(rules.at(-1), SECRET.at(-1));
  assert.equal(
    evaluate(rules, "edit", "/placeholder/config/skills/x/SKILL.md"),
    "deny",
  );
});

await test("pmRules does not change the rules it was given", () => {
  const extra = ["/placeholder/other-repo"];
  pmRules({ ...BASE, extraRepos: extra });
  assert.deepEqual(extra, ["/placeholder/other-repo"]);
  assert.equal(SECRET.length, 6);
});

await test("the denied commands are the DENY list of src/coven/pm/profile.py", () => {
  const source = readFileSync(
    new URL("../../src/coven/pm/profile.py", import.meta.url),
    "utf8",
  );
  const body = /^DENY = \[([^\]]*)\]/m.exec(source)?.[1] ?? "";
  const python = [...body.matchAll(/"Bash\(([^"]+):\*\)"/g)].map(
    (match) => `${match[1] ?? ""}*`,
  );
  assert.notEqual(python.length, 0);
  assert.equal([...body.matchAll(/"[^"]*"/g)].length, python.length);
  assert.deepEqual([...new Set(python)].sort(), [...DENIED_COMMANDS].sort());
});
