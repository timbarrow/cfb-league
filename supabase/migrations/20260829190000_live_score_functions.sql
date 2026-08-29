create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

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

create or replace function public.settle_completed_bets()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    ticket record;
    margin numeric;
    result_status text;
    payout numeric(14,2);
    graded integer := 0;
    won integer := 0;
    lost integer := 0;
    pushed integer := 0;
    credited numeric(14,2) := 0;
begin
    perform pg_advisory_xact_lock(918273641);
    for ticket in
        select b.id, b.user_id, b.bet_type, b.line_placed, b.wager_amount,
               b.payout_multiplier, g.home_score, g.away_score
          from public.bets b
          join public.games g on g.id = b.game_id
         where b.status = 'pending'
           and g.status = 'completed'
           and g.home_score is not null
           and g.away_score is not null
         order by b.created_at
         for update of b
    loop
        margin := case ticket.bet_type
            when 'spread_home' then ticket.home_score - ticket.away_score + ticket.line_placed
            when 'spread_away' then ticket.away_score - ticket.home_score + ticket.line_placed
            when 'over' then ticket.home_score + ticket.away_score - ticket.line_placed
            when 'under' then ticket.line_placed - ticket.home_score - ticket.away_score
        end;
        result_status := case when margin = 0 then 'push' when margin > 0 then 'won' else 'lost' end;
        payout := case
            when result_status = 'won' then round(ticket.wager_amount * ticket.payout_multiplier, 2)
            when result_status = 'push' then ticket.wager_amount
            else 0
        end;

        update public.bets
           set status = result_status,
               payout_amount = payout,
               settled_at = now()
         where id = ticket.id
           and status = 'pending';
        if found then
            graded := graded + 1;
            won := won + case when result_status = 'won' then 1 else 0 end;
            lost := lost + case when result_status = 'lost' then 1 else 0 end;
            pushed := pushed + case when result_status = 'push' then 1 else 0 end;
            if payout > 0 then
                update public.users set balance = balance + payout where id = ticket.user_id;
                credited := credited + payout;
            end if;
        end if;
    end loop;
    return jsonb_build_object(
        'graded', graded, 'won', won, 'lost', lost,
        'push', pushed, 'credited', credited
    );
end;
$$;

revoke all on function public.apply_live_scoreboard(jsonb) from public, anon, authenticated;
revoke all on function public.settle_completed_bets() from public, anon, authenticated;
grant execute on function public.apply_live_scoreboard(jsonb) to service_role;
grant execute on function public.settle_completed_bets() to service_role;
