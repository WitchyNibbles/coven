# TypeScript conventions (strict)

The repo's lint and type config wins. Never add a suppression comment, `any`, a type assertion
to dodge an error, or a weaker `tsconfig`. Each group below links only the pages that back its
rules; a rule sits there only if the linked page states it. The rest are under "Conventions".

## Types (https://www.typescriptlang.org/docs/handbook/2/narrowing.html)
- Narrow with type guards, `in`, `typeof` and `instanceof`.
- Model variants as discriminated unions; `switch` on the tag and end with a `never` check.
- Use `unknown`, not `any`, for values of unknown type (https://typescript-eslint.io/rules/no-explicit-any/).
- Use `satisfies` to check a value against a type without widening it (https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-9.html).
- Use `readonly` and `as const` for data that must not change (https://www.typescriptlang.org/docs/handbook/release-notes/typescript-3-4.html).
- An `as const` object can replace an `enum` (https://www.typescriptlang.org/docs/handbook/enums.html).
- Do not use the non-null `!` assertion (https://typescript-eslint.io/rules/no-non-null-assertion/).
- Follow the repo's `consistent-type-definitions`; default to `interface` for object shapes (https://typescript-eslint.io/rules/consistent-type-definitions/).
- Annotate exported function signatures (https://typescript-eslint.io/rules/explicit-module-boundary-types/).

## Boundaries (https://zod.dev/basics)
- `.parse` validates an input and throws a `ZodError`; `.safeParse` returns a result object instead.
- `z.infer<typeof Schema>` gives the static type; don't restate it.
- Use `z.strictObject` where unknown keys must be rejected (https://zod.dev/api).

## Async (https://typescript-eslint.io/rules/no-floating-promises/)
- Await or return every promise; never leave one floating.
- Do not mark a function `async` if it never awaits (https://typescript-eslint.io/rules/require-await/).
- Use `Promise.all` when every task must succeed: it rejects as soon as any input rejects (https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/all).
- Use `Promise.allSettled` when you need every outcome, including failures (https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/allSettled).
- Use an `AbortSignal` to abort async work such as `fetch`; `AbortSignal.timeout(ms)` aborts on its own (https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal).

## Errors (https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Error/cause)
- Rethrow with the original error as the cause: `new DomainError("what failed", { cause: err })`.

## Modules (https://nodejs.org/api/esm.html)
- Use ESM; relative specifiers carry the file extension, and with `module: nodenext` they end in `.js` (https://www.typescriptlang.org/docs/handbook/modules/reference.html).
- Synchronous `fs` calls block the event loop until they finish (https://nodejs.org/api/fs.html).

## Node and MCP (https://modelcontextprotocol.io/docs/develop/build-server)
- Never write to stdout in a stdio server (no `console.log`); log with `console.error`.
- Declare each tool input as a zod object schema in the form your SDK version documents (v2: `z.object({...})`). The SDK rejects invalid arguments with an `isError` result before the handler runs (https://ts.sdk.modelcontextprotocol.io/v2/servers/tools).
- `.describe()` on a field survives into the tool's JSON Schema (same tools page).

## Tests (https://vitest.dev/guide/mocking/timers)
- Use fake timers (`vi.useFakeTimers`) instead of waiting; in node:test use `mock.timers` (https://nodejs.org/docs/latest/api/test.html).
- Await async assertions; use `expect(...).rejects` for expected failures (https://vitest.dev/api/expect.html).

## Conventions (no single source)
- Prefer a union of string literals to `enum`.
- Narrow a caught value (`instanceof`) before using it.
- Parse every I/O edge (HTTP body, env, file, JSON, tool input) with a zod schema, then trust the typed value.
- Don't re-validate tool inputs inside the handler.
- Use `node:` prefixes for built-in imports.
- Use `fs/promises` on request paths; sync fs only at startup or in CLI scripts.
- Don't use snapshot tests to assert logic.
- Throw `Error` subclasses with a stable `name`; never throw strings or plain objects.
- Return `Result`-style values only in a module that already uses them; otherwise throw.
- Never leave a `catch` empty; handle it, rethrow it, or log with the cause and stop.
- Wrap a callback API in a promise once at the edge; don't mix styles.
- Group by feature folder: types, logic and tests of one feature sit together.
- Use barrel `index.ts` files only at a package boundary; import from the owning file inside it.
- Use named exports in libraries; no default exports.
- No I/O, network or env reads at import time; read configuration once into a zod-parsed object.
- Test behaviour through the public API; one behaviour per test, named for it.
- Mock at the boundary you own (HTTP, clock, fs); inject dependencies instead of patching modules.
- Build test data with small factory functions; do not share mutable state between tests.
