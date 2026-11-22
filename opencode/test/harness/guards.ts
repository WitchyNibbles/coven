/** Refusals that keep the end-to-end run on this machine. */

const FAKE = "fake";
const LITELLM = "litellm";

interface Policy {
  action?: string;
  resource?: string;
  effect?: string;
}

export interface ProjectConfig {
  providers?: Record<string, { settings?: { baseURL?: string } }>;
  policies?: Policy[];
}

function isLoopbackURL(url: string | undefined): boolean {
  return url !== undefined && /^http:\/\/127\.0\.0\.1[:/]/.test(url);
}

function providerProblem(config: ProjectConfig): string | undefined {
  const names = Object.keys(config.providers ?? {});
  if (names.length !== 1 || names[0] !== FAKE) {
    return "the config must define only the fake provider";
  }
  if (!isLoopbackURL(config.providers?.[FAKE]?.settings?.baseURL)) {
    return "the fake provider must point at 127.0.0.1";
  }
  return undefined;
}

/** A reason to refuse a coven profile config, unless its only provider is litellm on loopback. */
export function profileProblem(config: ProjectConfig): string | undefined {
  const names = Object.keys(config.providers ?? {});
  if (names.length !== 1 || names[0] !== LITELLM) {
    return "the profile must define only the litellm provider";
  }
  if (!isLoopbackURL(config.providers?.[LITELLM]?.settings?.baseURL)) {
    return "the litellm provider must point at 127.0.0.1";
  }
  return undefined;
}

function policyProblem(policies: readonly Policy[]): string | undefined {
  const use = policies.filter((policy) => policy.action === "provider.use");
  const denyAll = use.some((p) => p.resource === "*" && p.effect === "deny");
  const allowed = use.filter((p) => p.effect === "allow");
  if (!denyAll) {
    return "the config must deny provider.use for every provider";
  }
  if (allowed.length !== 1 || allowed[0]?.resource !== FAKE) {
    return "the config may allow only the fake provider";
  }
  return undefined;
}

/** A reason to refuse the project config, or undefined when it allows only the fake provider. */
export function configProblem(config: ProjectConfig): string | undefined {
  return providerProblem(config) ?? policyProblem(config.policies ?? []);
}

const STRING_OR_TRAILING_COMMA = /("(?:[^"\\]|\\.)*")|,(\s*[}\]])/g;

/** Parses the project config: JSON with the trailing commas the formatter adds. */
export function parseConfig(text: string): ProjectConfig {
  // Strings match first, so a ",}" inside a value is kept as it is.
  const json = text.replace(
    STRING_OR_TRAILING_COMMA,
    (_match: string, quoted: string | undefined, close: string | undefined) =>
      quoted ?? close ?? "",
  );
  return JSON.parse(json) as ProjectConfig;
}
