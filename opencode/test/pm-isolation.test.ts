import assert from "node:assert/strict";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { after, test } from "node:test";

import { pmIsolation } from "../src/pm/shell-env.ts";

const WRAPPER_NAME = "pm-shell-isolate.sh";
const SCRIPT = "#!/bin/sh\necho wrapper\n";
const roots: string[] = [];

after(() => {
  for (const root of roots) {
    rmSync(root, { recursive: true, force: true });
  }
});

/** A coven root with a wrapper in it, and a profile with a serve folder and a data folder. */
function world(script: string | null = SCRIPT) {
  const root = mkdtempSync(join(tmpdir(), "coven-isolation-"));
  roots.push(root);
  const covenRoot = join(root, "coven");
  const source = join(covenRoot, "hooks", WRAPPER_NAME);
  mkdirSync(dirname(source), { recursive: true });
  if (script !== null) {
    writeFileSync(source, script, { mode: 0o755 });
  }
  const serveDir = join(root, "profile", "state", "serve");
  mkdirSync(serveDir, { recursive: true });
  const options = {
    covenRoot,
    serveDir,
    dataDir: join(root, "profile", "data"),
  };
  return { root, source, serveDir, options };
}

const mode = (path: string): number => statSync(path).mode & 0o777;

await test("the folders to hide are the serve folder and the data folder options, pinned", () => {
  const found = pmIsolation({
    covenRoot: "/c",
    serveDir: "/tmp/coven-test/profile/state/serve",
    dataDir: "/tmp/coven-test/profile/data",
  });
  assert.equal(
    found.hide,
    "/tmp/coven-test/profile/state/serve:/tmp/coven-test/profile/data",
  );
});

await test("the data folder is the option as given, not derived from anything", () => {
  const found = pmIsolation({
    covenRoot: "/c",
    serveDir: "/s/serve",
    dataDir: "/elsewhere/data",
  });
  assert.equal(found.hide, "/s/serve:/elsewhere/data");
});

await test("the wrapper a PM's shell runs is a copy in the serve folder", () => {
  const w = world();
  const found = pmIsolation(w.options);
  assert.equal(found.found, true);
  assert.equal(found.problem, undefined);
  assert.equal(found.wrapper, join(w.serveDir, "isolate", WRAPPER_NAME));
  assert.notEqual(found.wrapper, w.source);
  assert.equal(readFileSync(found.wrapper, "utf8"), SCRIPT);
});

await test("the copy is read-only to its owner and its folder is private", () => {
  const w = world();
  const { wrapper } = pmIsolation(w.options);
  assert.equal(mode(wrapper), 0o500);
  assert.equal(mode(dirname(wrapper)), 0o700);
});

await test("each start overwrites the copy with what the source holds then", () => {
  const w = world();
  const { wrapper } = pmIsolation(w.options);
  writeFileSync(w.source, "#!/bin/sh\necho changed\n");
  assert.equal(readFileSync(wrapper, "utf8"), SCRIPT);
  assert.equal(pmIsolation(w.options).found, true);
  assert.equal(readFileSync(wrapper, "utf8"), "#!/bin/sh\necho changed\n");
  assert.equal(mode(wrapper), 0o500);
});

await test("a copy that was left changed or loose is replaced", () => {
  const w = world();
  const { wrapper } = pmIsolation(w.options);
  chmodSync(wrapper, 0o777);
  writeFileSync(wrapper, "tampered\n");
  chmodSync(dirname(wrapper), 0o755);
  assert.equal(pmIsolation(w.options).found, true);
  assert.equal(readFileSync(wrapper, "utf8"), SCRIPT);
  assert.equal(mode(wrapper), 0o500);
  assert.equal(mode(dirname(wrapper)), 0o700);
});

await test("the coven checkout's own wrapper is copied byte for byte", () => {
  const w = world();
  const covenRoot = resolve(import.meta.dirname, "../..");
  const found = pmIsolation({ ...w.options, covenRoot });
  assert.equal(found.found, true);
  assert.deepEqual(
    readFileSync(found.wrapper),
    readFileSync(join(covenRoot, "hooks", WRAPPER_NAME)),
  );
});

await test("a missing source is not found and names the source's path", () => {
  const w = world(null);
  const found = pmIsolation(w.options);
  assert.equal(found.found, false);
  assert.equal(found.problem, `wrapper not found: ${w.source}`);
});

await test("a source that is not an executable file is not found", () => {
  const w = world();
  chmodSync(w.source, 0o644);
  assert.equal(pmIsolation(w.options).found, false);
  rmSync(w.source);
  mkdirSync(w.source);
  chmodSync(w.source, 0o755);
  const found = pmIsolation(w.options);
  assert.equal(found.found, false);
  assert.equal(found.problem, `wrapper not found: ${w.source}`);
});

await test("a serve or data path with a colon is refused: the hide list is colon-joined", () => {
  const w = world();
  const colonServe = join(w.root, "profile", "state", "se:rve");
  mkdirSync(colonServe, { recursive: true });
  for (const options of [
    { ...w.options, serveDir: colonServe },
    { ...w.options, dataDir: join(w.root, "da:ta") },
  ]) {
    const found = pmIsolation(options);
    assert.equal(found.found, false);
    assert.equal(found.problem, "a hide path contains ':'");
  }
  assert.equal(pmIsolation(w.options).found, true);
});

await test("a serve folder that is not there fails the copy and names why", () => {
  const w = world();
  const options = { ...w.options, serveDir: join(w.root, "no", "serve") };
  const found = pmIsolation(options);
  assert.equal(found.found, false);
  assert.match(found.problem ?? "", /^wrapper copy failed: .*ENOENT/);
});

await test("an isolate entry that is a file fails the copy", () => {
  const w = world();
  writeFileSync(join(w.serveDir, "isolate"), "not a folder");
  const found = pmIsolation(w.options);
  assert.equal(found.found, false);
  assert.match(found.problem ?? "", /^wrapper copy failed: /);
});
