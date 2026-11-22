/**
 * The entry of `npm run e2e`: runs test/e2e/main.ts inside `unshare -rn`, a user and network
 * namespace with only the loopback interface, so nothing OpenCode does can leave the machine.
 * It acts only when COVEN_E2E_ENTER=1, so `node --test` loads it as a no-op.
 */
import { spawnSync } from "node:child_process";

const NOT_AVAILABLE = 2;
const FAILED = 1;

/** Runs the e2e entry in a fresh namespace and returns the exit status to use. */
function enter(argv: readonly string[]): number {
  const probe = spawnSync("unshare", ["-rn", "true"], { encoding: "utf8" });
  if (probe.status !== 0) {
    const first = (probe.stderr ?? "").split("\n")[0] ?? "";
    console.error(`unshare -rn is not available: ${first}`);
    return NOT_AVAILABLE;
  }
  const run = spawnSync(
    "unshare",
    ["-rn", "node", "test/e2e/main.ts", ...argv],
    { stdio: "inherit", env: { ...process.env, COVEN_E2E: "1" } },
  );
  return run.status ?? FAILED;
}

if (process.env["COVEN_E2E_ENTER"] === "1") {
  process.exitCode = enter(process.argv.slice(2));
}
