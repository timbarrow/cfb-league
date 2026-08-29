import { createClient } from "jsr:@supabase/supabase-js@2";

const jsonHeaders = { "content-type": "application/json" };

function response(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
}

function chicagoParts(now = new Date()): Record<string, number> {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "numeric",
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(
    formatter.formatToParts(now).map(({ type, value }) => [type, value]),
  );
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(
    parts.weekday,
  );
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    weekday,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
  };
}

export function shouldPoll(now = new Date()): boolean {
  const { month, weekday, hour, minute } = chicagoParts(now);
  if (![1, 8, 9, 10, 11, 12].includes(month)) return false;
  if (weekday >= 1 && weekday <= 3) {
    return (hour === 20 || hour === 22) && minute < 4 ||
      hour === 23 && minute >= 56;
  }
  if (weekday === 4 || weekday === 5) return hour >= 20;
  if (weekday === 6) return hour >= 11;
  return hour < 2 || hour === 6 && minute < 4;
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function liveStatus(game: Record<string, unknown>): string {
  const raw = String(game.status ?? "").trim().toLowerCase();
  if (raw.includes("final") || raw.includes("complete") || raw === "post") {
    return "completed";
  }
  if (
    ["progress", "live", "halftime", "delay"].some((word) => raw.includes(word))
  ) {
    return "in_progress";
  }
  return (numberOrNull(game.period) ?? 0) > 0 ? "in_progress" : "scheduled";
}

function shapeGame(
  game: Record<string, unknown>,
): Record<string, unknown> | null {
  const id = numberOrNull(game.id ?? game.gameId ?? game.game_id);
  if (id === null) return null;
  const home = (game.homeTeam ?? game.home_team ?? {}) as Record<
    string,
    unknown
  >;
  const away = (game.awayTeam ?? game.away_team ?? {}) as Record<
    string,
    unknown
  >;
  const homeScore = numberOrNull(home.points ?? home.score);
  const awayScore = numberOrNull(away.points ?? away.score);
  let status = liveStatus(game);
  if (status === "completed" && (homeScore === null || awayScore === null)) {
    status = "in_progress";
  }
  return {
    id,
    status,
    home_score: homeScore,
    away_score: awayScore,
    game_period: numberOrNull(game.period),
    game_clock: game.clock == null ? null : String(game.clock).trim(),
    game_possession: game.possession == null
      ? null
      : String(game.possession).trim().toLowerCase(),
    home_win_probability: numberOrNull(
      home.winProbability ?? home.win_probability,
    ),
    away_win_probability: numberOrNull(
      away.winProbability ?? away.win_probability,
    ),
  };
}

Deno.serve(async (request) => {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceKey) {
    return response({ error: "Missing Supabase secrets" }, 500);
  }
  const supabase = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  if (request.method === "GET") {
    const { data, error } = await supabase
      .from("games")
      .select("scores_updated_at")
      .eq("classification", "fbs")
      .eq("status", "in_progress")
      .order("scores_updated_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    return error ? response({ status: "error" }, 500) : response({
      status: "ok",
      latest_live_refresh: data?.scores_updated_at ?? null,
    });
  }
  if (request.method !== "POST") {
    return response({ error: "Method not allowed" }, 405);
  }
  const { data: authorized, error: authError } = await supabase.rpc(
    "verify_live_cron_secret",
    { candidate: request.headers.get("x-cron-secret") ?? "" },
  );
  if (authError || !authorized) return response({ error: "Unauthorized" }, 401);

  let force = false;
  try {
    force = Boolean((await request.json()).force);
  } catch {
    // Cron sends an empty JSON body.
  }
  if (!force && !shouldPoll()) {
    return response({ skipped: true, reason: "outside game window" });
  }

  const apiKey = Deno.env.get("CFBD_API_KEY");
  if (!apiKey) return response({ error: "Missing CFBD_API_KEY" }, 500);

  const scoreboardResponse = await fetch(
    "https://api.collegefootballdata.com/scoreboard?classification=fbs",
    {
      headers: {
        authorization: `Bearer ${apiKey}`,
        accept: "application/json",
      },
    },
  );
  if (!scoreboardResponse.ok) {
    return response(
      { error: `CFBD returned ${scoreboardResponse.status}` },
      502,
    );
  }
  const payload = await scoreboardResponse.json() as Array<
    Record<string, unknown>
  >;
  const games = payload.map(shapeGame).filter((game) => game !== null);
  const { data: updated, error: updateError } = await supabase.rpc(
    "apply_live_scoreboard",
    { score_rows: games },
  );
  if (updateError) return response({ error: updateError.message }, 500);

  const { data: settlement, error: settlementError } = await supabase.rpc(
    "settle_completed_bets",
  );
  if (settlementError) return response({ error: settlementError.message }, 500);
  return response({ updated, settlement, fetched: games.length });
});
