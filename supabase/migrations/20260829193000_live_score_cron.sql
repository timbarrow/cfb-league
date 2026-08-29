create or replace function public.verify_live_cron_secret(candidate text)
returns boolean
language sql
security definer
set search_path = public, vault
as $$
    select exists (
        select 1
          from vault.decrypted_secrets
         where name = 'live_cron_secret'
           and decrypted_secret = candidate
    );
$$;

revoke all on function public.verify_live_cron_secret(text) from public, anon, authenticated;
grant execute on function public.verify_live_cron_secret(text) to service_role;

do $$
declare
    existing_job bigint;
begin
    if not exists (select 1 from vault.decrypted_secrets where name = 'project_url') then
        perform vault.create_secret('https://rhozxpsciplpydwukhid.supabase.co', 'project_url');
    end if;
    if not exists (select 1 from vault.decrypted_secrets where name = 'live_cron_secret') then
        perform vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'live_cron_secret');
    end if;

    select jobid into existing_job
      from cron.job
     where jobname = 'cfb-live-scores-every-two-minutes';
    if existing_job is not null then
        perform cron.unschedule(existing_job);
    end if;

    perform cron.schedule(
        'cfb-live-scores-every-two-minutes',
        '*/2 * * * *',
        $job$
        select net.http_post(
            url := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url')
                   || '/functions/v1/cfb-live-scores',
            headers := jsonb_build_object(
                'Content-Type', 'application/json',
                'x-cron-secret',
                (select decrypted_secret from vault.decrypted_secrets where name = 'live_cron_secret')
            ),
            body := '{}'::jsonb
        );
        $job$
    );
end;
$$;
