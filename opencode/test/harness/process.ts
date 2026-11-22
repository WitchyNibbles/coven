import { spawn } from "node:child_process";
import { join } from "node:path";

export interface Finished {
  code: number | null;
  out: string;
  err: string;
}

/** An environment built from nothing, so no key or setting of the caller reaches OpenCode. */
export function cleanEnv(home: string): Record<string, string> {
  return {
    PATH: process.env["PATH"] ?? "",
    HOME: home,
    XDG_CONFIG_HOME: join(home, ".config"),
    XDG_DATA_HOME: join(home, ".local", "share"),
    XDG_STATE_HOME: join(home, ".local", "state"),
    XDG_CACHE_HOME: join(home, ".cache"),
    TMPDIR: join(home, "tmp"),
    OPENCODE_DISABLE_AUTOUPDATE: "1",
  };
}

/** Runs a command to its end, or kills it after `timeoutMs`; stdin is closed. */
export function exec(
  command: string,
  args: string[],
  options: { cwd: string; env: Record<string, string>; timeoutMs: number },
): Promise<Finished> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (part: Buffer) => {
      out += part.toString("utf8");
    });
    child.stderr.on("data", (part: Buffer) => {
      err += part.toString("utf8");
    });
    const timer = setTimeout(() => child.kill("SIGKILL"), options.timeoutMs);
    child.once("error", reject);
    child.once("close", (code) => {
      clearTimeout(timer);
      resolve({ code, out, err });
    });
  });
}
