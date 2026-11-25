/**
 * The `workdir` of a shell call when it gave one: a string with something in it. An empty
 * string is no workdir, and neither is any other type. The guard resolves it against the
 * location directory; the shell queue keeps it as given, because that is the string the
 * shell event carries.
 */
export function givenWorkdir(workdir: unknown): string | undefined {
  return typeof workdir === "string" && workdir !== "" ? workdir : undefined;
}
