import type { ShellCreateEvent } from "./context.ts";

/** The server password: OpenCode 2.3.2 hands it to every shell command, and it opens the whole API. */
const PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD";

/**
 * The `shell.create.before` handler that takes the provider key and the server password out of
 * a shell command's environment. Both stay in OpenCode's own process, which the provider and the
 * server need; only the commands the model runs lose them.
 */
export function scrubShellEnv(
  keyEnv: string,
): (event: ShellCreateEvent) => void {
  const secrets = [keyEnv, PASSWORD_ENV];
  return (event) => {
    for (const name of secrets) {
      delete event.env[name];
    }
  };
}
