import { spawnSync } from "node:child_process";

/** The refusal printed when the run is not inside the `unshare -rn` namespace. */
export const NAMESPACE_MESSAGE = "not in a network namespace: run npm run e2e";

/** Interface names from the text of /proc/net/dev (it follows the reader's network namespace). */
export function interfaceNames(procNetDev: string): string[] {
  return procNetDev
    .split("\n")
    .slice(2)
    .map((line) => line.split(":")[0]?.trim() ?? "")
    .filter((name) => name !== "");
}

export function onlyLoopback(procNetDev: string): boolean {
  const names = interfaceNames(procNetDev);
  return names.length === 1 && names[0] === "lo";
}

/** A fresh namespace has `lo` down; the fake model and the servers listen on it. */
export function bringLoopbackUp(): boolean {
  return spawnSync("ip", ["link", "set", "lo", "up"]).status === 0;
}
