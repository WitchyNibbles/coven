import { covenSetup } from "./coven.ts";
import type { CovenContext } from "./context.ts";

export default {
  id: "coven",
  setup: (ctx: CovenContext): Promise<() => void> =>
    covenSetup(
      ctx,
      { now: () => Date.now(), log: (line) => console.error(line) },
      ctx.options,
    ),
};
