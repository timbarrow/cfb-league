import {
  POLL_LOOKAHEAD_MS,
  POLL_MAX_GAME_AGE_MS,
  shouldPoll,
} from "./polling.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function fakeSupabase(result: Record<string, unknown>) {
  const calls: Array<[string, ...unknown[]]> = [];
  const builder: Record<string, unknown> = {};
  for (const method of ["select", "eq", "neq", "lte", "gte"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  builder.then = (
    resolve: (value: Record<string, unknown>) => unknown,
    reject: (reason: unknown) => unknown,
  ) => Promise.resolve(result).then(resolve, reject);

  return {
    client: {
      from(table: string) {
        calls.push(["from", table]);
        return builder;
      },
    },
    calls,
  };
}

Deno.test("off-season skips without querying the database", async () => {
  const fake = fakeSupabase({ count: 1, error: null });
  const result = await shouldPoll(
    fake.client,
    new Date("2026-07-15T18:00:00Z"),
  );
  assert(result === false, "July should not poll");
  assert(fake.calls.length === 0, "off-season should not query games");
});

Deno.test("any qualifying game enables polling on any day of the week", async () => {
  const fake = fakeSupabase({ count: 1, error: null });
  const now = new Date("2026-09-07T23:00:00Z"); // Labor Day Monday
  const result = await shouldPoll(fake.client, now);
  assert(result === true, "a Monday game should enable polling");

  const lowerBound = fake.calls.find(
    ([method, column]) => method === "gte" && column === "start_date",
  );
  const upperBound = fake.calls.find(
    ([method, column]) => method === "lte" && column === "start_date",
  );
  assert(
    lowerBound?.[2] ===
      new Date(now.getTime() - POLL_MAX_GAME_AGE_MS).toISOString(),
    "query should use the stale-game cutoff",
  );
  assert(
    upperBound?.[2] ===
      new Date(now.getTime() + POLL_LOOKAHEAD_MS).toISOString(),
    "query should begin shortly before kickoff",
  );
});

Deno.test("no qualifying games skips the scoreboard call", async () => {
  const fake = fakeSupabase({ count: 0, error: null });
  const result = await shouldPoll(
    fake.client,
    new Date("2026-09-06T18:00:00Z"),
  );
  assert(result === false, "an empty schedule window should not poll");
});

Deno.test("schedule query errors fail closed", async () => {
  const fake = fakeSupabase({
    count: null,
    error: { message: "database unavailable" },
  });
  let caught: unknown;
  try {
    await shouldPoll(fake.client, new Date("2026-09-07T23:00:00Z"));
  } catch (error) {
    caught = error;
  }
  assert(caught instanceof Error, "database errors should reject");
  assert(
    caught.message.includes("database unavailable"),
    "the underlying database error should be logged by the caller",
  );
});
