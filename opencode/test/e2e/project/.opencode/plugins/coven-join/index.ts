import { fileURLToPath } from "node:url";

import { covenSetup } from "../../../../../../src/coven.ts";
import type { CovenContext } from "../../../../../../src/context.ts";

const COVEN_ROOT = fileURLToPath(
  new URL("../../../../../../../", import.meta.url),
);

/** The join suite loads the plugin through auto-load, so the root is not an options entry. */
export default {
  id: "coven",
  setup: (ctx: CovenContext): Promise<() => void> =>
    covenSetup(
      ctx,
      { now: () => Date.now(), log: (line) => console.error(line) },
      { covenRoot: COVEN_ROOT },
    ),
};
