import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { covenSetup } from "../../../../../../src/coven.ts";
import type { CovenContext } from "../../../../../../src/context.ts";

// resolve drops the trailing slash fileURLToPath keeps for a directory URL.
const COVEN_ROOT = resolve(
  fileURLToPath(new URL("../../../../../../../", import.meta.url)),
);

/** The join suite loads the plugin through auto-load, so the root is not an options entry. */
export default {
  id: "coven",
  setup: (ctx: CovenContext): Promise<() => void> =>
    covenSetup(
      ctx,
      { now: () => Date.now(), log: (line) => console.error(line) },
      {
        covenRoot: COVEN_ROOT,
        keyEnv: "COVEN_E2E_KEY",
        profileDir: "/tmp/coven-test/profile",
        serveDir: "/tmp/coven-test/profile/state/serve",
        dataDir: "/tmp/coven-test/profile/data",
        store: null,
        secretRules: [],
      },
    ),
};
