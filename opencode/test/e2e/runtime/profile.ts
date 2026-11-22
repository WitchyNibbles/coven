/** The runtime suite's world: a scratch HOME with a fake 1.x install, and a coven profile. */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { coven } from "../../harness/coven.ts";
import { startFakeModel } from "../../harness/fake-model.ts";
import type { FakeModel } from "../../harness/fake-model.ts";
import { parseConfig, profileProblem } from "../../harness/guards.ts";
import { cleanEnv } from "../../harness/process.ts";
import { decideRuntime, SECRET_VALUE, SECRETS_FILE } from "./content-script.ts";

export interface RuntimeWorld {
  home: string;
  profile: string;
  work: string;
  env: Record<string, string>;
  model: FakeModel;
}

const SETUP_TIMEOUT_MS = 60_000;
const LEAK_SKILL = `---
name: leak-e2e
description: A skill a 1.x install keeps; the coven profile must never list it.
---
Leak.
`;

/** A 1.x install in the scratch HOME: a config, some data and a skill under .agents. */
async function plantOneX(home: string): Promise<void> {
  const config = join(home, ".config", "opencode");
  const data = join(home, ".local", "share", "opencode");
  const skill = join(home, ".agents", "skills", "leak-e2e");
  for (const folder of [config, data, skill, join(home, "tmp")]) {
    await mkdir(folder, { recursive: true });
  }
  const onex = {
    provider: { onex: { options: { baseURL: "http://127.0.0.1:9/v1" } } },
  };
  await writeFile(join(config, "opencode.json"), JSON.stringify(onex));
  await writeFile(join(data, "auth.json"), "{}");
  await writeFile(join(skill, "SKILL.md"), LEAK_SKILL);
}

/** Where `coven opencode setup` writes the generated config. */
export function configPath(profile: string): string {
  return join(profile, "config", "opencode", "opencode.jsonc");
}

/** Sets the providers the plugin's `tool_choice` hook answers for, in the generated config. */
export async function setToolChoiceAuto(
  profile: string,
  providers: string[],
): Promise<void> {
  const path = configPath(profile);
  const config = JSON.parse(await readFile(path, "utf8")) as {
    plugins: { options: { toolChoiceAuto: string[] } }[];
  };
  for (const plugin of config.plugins) {
    plugin.options.toolChoiceAuto = providers;
  }
  await writeFile(path, `${JSON.stringify(config, null, 2)}\n`);
}

/** Points the generated config's provider at a fake model on loopback port `port`. */
export async function pointProviderAt(
  profile: string,
  port: number,
): Promise<void> {
  const path = configPath(profile);
  const config = JSON.parse(await readFile(path, "utf8")) as {
    providers: { litellm: { settings: { baseURL: string } } };
  };
  config.providers.litellm.settings.baseURL = `http://127.0.0.1:${port}/v1`;
  await writeFile(path, `${JSON.stringify(config, null, 2)}\n`);
}

/** Builds the world under `root`, runs `coven opencode setup` in it and refuses an unsafe config. */
export async function makeWorld(
  root: string,
  binary: string,
): Promise<RuntimeWorld> {
  const home = join(root, "home");
  const profile = join(root, "profile");
  const work = join(home, "work", "repo");
  await plantOneX(home);
  await mkdir(work, { recursive: true });
  await writeFile(join(work, SECRETS_FILE), `API_TOKEN=${SECRET_VALUE}\n`);
  const env = {
    ...cleanEnv(home),
    COVEN_OPENCODE_HOME: profile,
    COVEN_OPENCODE_BIN: binary,
    COVEN_E2E_KEY: "offline",
    OPENCODE_DISABLE_MODELS_FETCH: "1",
  };
  const model = await startFakeModel({
    decide: decideRuntime,
    mode: "litellm",
    port: 0,
  });
  try {
    const setup = await coven(
      [
        "opencode",
        "setup",
        "--preset",
        "litellm",
        "--base-url",
        `http://127.0.0.1:${model.port}/v1`,
        "--key-env",
        "COVEN_E2E_KEY",
        "--model",
        "lead=fake",
      ],
      { cwd: work, env, timeoutMs: SETUP_TIMEOUT_MS },
    );
    if (setup.code !== 0) {
      throw new Error(`setup exited ${setup.code}: ${setup.err.slice(-300)}`);
    }
    const text = await readFile(configPath(profile), "utf8");
    const problem = profileProblem(parseConfig(text));
    if (problem !== undefined) {
      throw new Error(problem);
    }
  } catch (error) {
    await model.close();
    throw error;
  }
  return { home, profile, work, env, model };
}
