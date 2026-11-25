/**
 * The script of the fake model for the content checks, and the dispatcher the runtime world uses.
 * The first user message names the part:
 *   SECRETS        read the secrets file, then cat it through the shell, then stop
 *   SECRETS_READ   only the read
 *   SDD_LEAD       start the implementer and the reviewer, await both, then stop
 *   SHELLENV       one shell command that prints whether the key and the password are set
 *   SDD_CHILD role=<r>   answer with that role's report marker at once
 *   anything else  the PING script
 */
import { textOf } from "../../harness/chat.ts";
import type { ChatMessage, Reply } from "../../harness/chat.ts";
import type { Decide } from "../../harness/fake-model.ts";
import { sessionIDsIn } from "../../harness/history.ts";
import { decidePing, resultsOf } from "./ping-script.ts";

/** The secrets file in the project, and the value the checks must never see leave it. */
export const SECRETS_FILE = ".env";
export const SECRET_VALUE = "SECRET-VALUE-9d41";

/** The roles the lead starts, and the report each child answers with. */
export const ROLES = ["implementer", "reviewer"] as const;
export function reportOf(role: string): string {
  return `REPORT-${role.toUpperCase()}-7c2e`;
}

const SECRETS = /\bSECRETS\b/;
const SECRETS_READ = /\bSECRETS_READ\b/;
const SDD_LEAD = /\bSDD_LEAD\b/;
const SDD_CHILD = /\bSDD_CHILD role=(\w+)/;
const SHELLENV = /\bSHELLENV\b/;

/** The variables the shell must not see: the profile's key and the server password. */
export const KEY_ENV = "COVEN_E2E_KEY";
export const PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD";
/** The key's dummy value, so the check can tell the provider still got it. */
export const KEY_VALUE = "offline";
/** The shell command of the SHELLENV part: it prints whether each variable is set, never a value. */
const PRESENCE = `echo "KEY=[\${${KEY_ENV}:+set}] PW=[\${${PASSWORD_ENV}:+set}]"`;

function readCall(): Reply {
  return {
    kind: "calls",
    calls: [{ name: "read", args: { path: SECRETS_FILE } }],
  };
}

function catCall(): Reply {
  const args = { command: `cat ${SECRETS_FILE}`, description: "show" };
  return { kind: "calls", calls: [{ name: "shell", args }] };
}

function decideShellEnv(messages: readonly ChatMessage[]): Reply {
  if (resultsOf(messages, "shell").length > 0) {
    return { kind: "text", text: "SHELLENV DONE" };
  }
  const args = { command: `sh -c '${PRESENCE}'`, description: "presence" };
  return { kind: "calls", calls: [{ name: "shell", args }] };
}

function decideSecrets(
  messages: readonly ChatMessage[],
  readOnly: boolean,
): Reply {
  if (resultsOf(messages, "read").length === 0) {
    return readCall();
  }
  if (!readOnly && resultsOf(messages, "shell").length === 0) {
    return catCall();
  }
  return { kind: "text", text: "SECRETS DONE" };
}

function decideLead(messages: readonly ChatMessage[]): Reply {
  const started = resultsOf(messages, "subagent");
  if (started.length === 0) {
    const calls = ROLES.map((role) => ({
      name: "subagent",
      args: {
        agent: role,
        description: `the ${role}`,
        prompt: `SDD_CHILD role=${role}`,
      },
    }));
    return { kind: "calls", calls };
  }
  if (resultsOf(messages, "await_agents").length === 0) {
    const ids = [...new Set(started.flatMap(sessionIDsIn))];
    const args = { ids, mode: "all" };
    return { kind: "calls", calls: [{ name: "await_agents", args }] };
  }
  return { kind: "text", text: "SDD DONE" };
}

/** The user messages of the conversation, in order. */
function prompts(messages: readonly ChatMessage[]): string[] {
  return messages
    .filter((message) => message.role === "user")
    .map((message) => textOf(message.content));
}

/** The reply of the world's fake model: the part a user message names, else PING. */
export const decideRuntime: Decide = (messages, toolNames) => {
  const users = prompts(messages).join("\n");
  const role = SDD_CHILD.exec(users)?.[1];
  if (toolNames.length === 0) {
    return decidePing(messages, toolNames);
  }
  if (role !== undefined) {
    return { kind: "text", text: reportOf(role) };
  }
  if (SDD_LEAD.test(users)) {
    return decideLead(messages);
  }
  if (SHELLENV.test(users)) {
    return decideShellEnv(messages);
  }
  if (SECRETS.test(users) || SECRETS_READ.test(users)) {
    return decideSecrets(messages, SECRETS_READ.test(users));
  }
  return decidePing(messages, toolNames);
};
