alter table public.games add column if not exists game_situation text;

create or replace function public.apply_live_scoreboard(score_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    score_row jsonb;
    affected integer;
    written integer := 0;
begin
    if jsonb_typeof(score_rows) <> 'array' then
        raise exception 'score_rows must be a JSON array';
    end if;

    for score_row in select value from jsonb_array_elements(score_rows)
    loop
        update public.games g
           set home_score = coalesce((score_row->>'home_score')::integer, g.home_score),
               away_score = coalesce((score_row->>'away_score')::integer, g.away_score),
               game_period = coalesce((score_row->>'game_period')::integer, g.game_period),
               game_clock = coalesce(nullif(score_row->>'game_clock', ''), g.game_clock),
               game_possession = coalesce(nullif(score_row->>'game_possession', ''), g.game_possession),
               game_situation = nullif(score_row->>'game_situation', ''),
               home_win_probability = coalesce((score_row->>'home_win_probability')::numeric, g.home_win_probability),
               away_win_probability = coalesce((score_row->>'away_win_probability')::numeric, g.away_win_probability),
               status = case
                   when g.status = 'completed' then 'completed'
                   when score_row->>'status' = 'completed' then 'completed'
                   when score_row->>'status' = 'in_progress' then 'in_progress'
                   else g.status
               end,
               scores_updated_at = now(),
               updated_at = now()
         where g.id = (score_row->>'id')::bigint
           and g.classification = 'fbs';
        get diagnostics affected = row_count;
        written := written + affected;
    end loop;
    return written;
end;
$$;

revoke all on function public.apply_live_scoreboard(jsonb) from public, anon, authenticated;
grant execute on function public.apply_live_scoreboard(jsonb) to service_role;
