/** A small client of the coven server's HTTP API: loopback, the saved password, never throws on a status. */
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const USERNAME = "opencode";
const REQUEST_TIMEOUT_MS = 20_000;

export interface Answer {
  status: number;
  /** The parsed JSON body; the raw text when it is not JSON; undefined when it is empty. */
  body: unknown;
}

export interface Api {
  get(path: string): Promise<Answer>;
  post(path: string, body?: object): Promise<Answer>;
  patch(path: string, body: object): Promise<Answer>;
  del(path: string): Promise<Answer>;
}

function parse(text: string): unknown {
  if (text.trim() === "") {
    return undefined;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function send(
  method: string,
  url: string,
  password: string,
  body: object | undefined,
): Promise<Answer> {
  const token = Buffer.from(`${USERNAME}:${password}`).toString("base64");
  const response = await fetch(url, {
    method,
    headers: {
      authorization: `Basic ${token}`,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  return { status: response.status, body: parse(await response.text()) };
}

/** The server's folder of password and port files, under the profile. */
export function serveDir(profile: string): string {
  return join(profile, "state", "serve");
}

/** The client for the server the profile's serve folder names; read again on every call, so a restart is followed. */
export function apiFor(profile: string): Api {
  const call = async (
    method: string,
    path: string,
    body?: object,
  ): Promise<Answer> => {
    const folder = serveDir(profile);
    const password = (await readFile(join(folder, "password"), "utf8")).trim();
    const port = (await readFile(join(folder, "port"), "utf8")).trim();
    return send(method, `http://127.0.0.1:${port}${path}`, password, body);
  };
  return {
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body ?? {}),
    patch: (path, body) => call("PATCH", path, body),
    del: (path) => call("DELETE", path),
  };
}

/** The value when it is a string, else the empty string. */
export function stringOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The items of a list answer: the body itself or its `data`, else none. */
export function itemsOf(body: unknown): Record<string, unknown>[] {
  const items = Array.isArray(body)
    ? body
    : (body as { data?: unknown } | undefined)?.data;
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
}

/** A value read by key path from parsed JSON; undefined when any step is missing. */
export function at(value: unknown, ...keys: (string | number)[]): unknown {
  let here: unknown = value;
  for (const key of keys) {
    if (typeof here !== "object" || here === null) {
      return undefined;
    }
    here = (here as Record<string | number, unknown>)[key];
  }
  return here;
}
