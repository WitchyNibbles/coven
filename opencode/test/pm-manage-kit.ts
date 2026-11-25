/** The GM tools of `pm/manage.ts` run as a GM session over a fake world; shared by the pm-manage tests. */
import { pmListTool, pmResumeTool, pmStopTool } from "../src/pm/manage.ts";
import type { PmStatus } from "../src/pm/manage.ts";
import { resolveRole } from "../src/pm/role.ts";
import { call, quietStatus } from "./pm-gm-world.ts";
import type { World } from "./pm-gm-world.ts";

export function deps(world: World) {
  return { session: world, resolve: resolveRole };
}

export function list(
  world: World,
  status: PmStatus = quietStatus(),
  as = "gm1",
) {
  return call(pmListTool({ ...deps(world), status }), as, {});
}

export function resume(world: World, input: unknown, as = "gm1") {
  return call(pmResumeTool(deps(world)), as, input);
}

export function stop(world: World, input: unknown, as = "gm1") {
  return call(pmStopTool(deps(world)), as, input);
}
