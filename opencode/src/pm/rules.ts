import type { PermissionRule } from "../context.ts";

export interface PmRulesInput {
  /** The memory store as configured, or null when there is none. */
  store: string | null;
  /** The store with symbolic links resolved, or null when it could not be. */
  storeResolved: string | null;
  /** The profile's `tmp/` folder. */
  tmp: string;
  /** The plugin option: the secret rules, ending with the skills edit deny. */
  secretRules: readonly PermissionRule[];
  extraRepos: readonly string[];
}

/** The `DENY` list of `src/coven/pm/profile.py`, as shell command patterns. */
export const DENIED_COMMANDS: readonly string[] = [
  "git push --force*",
  "git push -f*",
  "git push --force-with-lease*",
  "git push origin main*",
  "git push origin master*",
  "gh pr merge*",
];

const rule = (
  action: string,
  resource: string,
  effect: PermissionRule["effect"],
): PermissionRule => ({ action, resource, effect });

/**
 * The permissions of a PM session, in the spec's order (the last matching rule wins): the
 * allows, the folders the PM may reach, the denies the profile also holds (session rules beat
 * the profile's), the memory store by both of its paths, webfetch and question, and last the
 * secret rules, so a PM's `read .env` is refused here as well as by deny-secrets.
 */
export function pmRules(input: PmRulesInput): PermissionRule[] {
  const stores = [input.store, input.storeResolved].filter(
    (path): path is string => path !== null,
  );
  return [
    rule("shell", "*", "allow"),
    rule("edit", "*", "allow"),
    rule("read", "*", "allow"),
    ...[input.tmp, ...input.extraRepos].map((folder) =>
      rule("external_directory", `${folder}/*`, "allow"),
    ),
    ...DENIED_COMMANDS.map((command) => rule("shell", command, "deny")),
    ...[...new Set(stores)].map((path) => rule("edit", `${path}/*`, "deny")),
    rule("webfetch", "*", "deny"),
    rule("question", "*", "deny"),
    ...input.secretRules,
  ];
}
