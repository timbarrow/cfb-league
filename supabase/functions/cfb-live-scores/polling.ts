const ACTIVE_MONTHS = new Set([1, 8, 9, 10, 11, 12]);

export const POLL_LOOKAHEAD_MS = 15 * 60 * 1000;
export const POLL_MAX_GAME_AGE_MS = 12 * 60 * 60 * 1000;

function chicagoMonth(now: Date): number {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    month: "numeric",
  });
  return Number(formatter.format(now));
}

// deno-lint-ignore no-explicit-any
type SupabaseClient = any;

// Let the stored schedule decide when a scoreboard call is useful. The maximum
// age keeps a postponed/canceled game with a stale status from polling forever,
// while leaving ample room for overtime and weather delays.
export async function shouldPoll(
  supabase: SupabaseClient,
  now = new Date(),
): Promise<boolean> {
  if (!ACTIVE_MONTHS.has(chicagoMonth(now))) return false;

  const soon = new Date(now.getTime() + POLL_LOOKAHEAD_MS).toISOString();
  const cutoff = new Date(now.getTime() - POLL_MAX_GAME_AGE_MS).toISOString();
  const { count, error } = await supabase
    .from("games")
    .select("id", { count: "exact", head: true })
    .eq("classification", "fbs")
    .neq("status", "completed")
    .lte("start_date", soon)
    .gte("start_date", cutoff);

  if (error) {
    throw new Error(`Live polling schedule query failed: ${error.message}`);
  }
  return (count ?? 0) > 0;
}
