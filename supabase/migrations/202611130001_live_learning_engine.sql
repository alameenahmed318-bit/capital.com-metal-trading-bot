-- AL-ZOOL live learning: idempotent outcome events and bounded per-strategy weights.
create table if not exists public.alzool_engine_live_learning_events (
    client_id text not null,
    deal_id text not null,
    strategy_id integer not null check (strategy_id between 1 and 200),
    symbol text not null default 'XAUUSD',
    net numeric(20, 8) not null default 0,
    processed_at timestamptz not null default now(),
    primary key (client_id, deal_id)
);

create table if not exists public.alzool_engine_live_model_weights (
    client_id text not null,
    strategy_id integer not null check (strategy_id between 1 and 200),
    weight numeric(8, 4) not null default 1.0000 check (weight between 0.5000 and 1.5000),
    sample_count integer not null default 0,
    wins integer not null default 0,
    losses integer not null default 0,
    total_net numeric(20, 8) not null default 0,
    last_updated timestamptz not null default now(),
    primary key (client_id, strategy_id)
);

alter table public.alzool_engine_live_learning_events enable row level security;
alter table public.alzool_engine_live_model_weights enable row level security;
revoke all on public.alzool_engine_live_learning_events from anon, authenticated;
revoke all on public.alzool_engine_live_model_weights from anon, authenticated;
grant all on public.alzool_engine_live_learning_events to service_role;
grant all on public.alzool_engine_live_model_weights to service_role;

create or replace function public.alzool_apply_live_learning(
    p_client_id text,
    p_deal_id text,
    p_strategy_id integer,
    p_symbol text,
    p_net numeric
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    inserted_rows integer;
    updated_weight numeric(8,4);
    updated_samples integer;
begin
    if coalesce(trim(p_client_id), '') = '' or coalesce(trim(p_deal_id), '') = ''
       or p_strategy_id < 1 or p_strategy_id > 200 then
        raise exception 'Invalid live-learning event';
    end if;

    insert into public.alzool_engine_live_learning_events
        (client_id, deal_id, strategy_id, symbol, net)
    values
        (p_client_id, p_deal_id, p_strategy_id, coalesce(nullif(p_symbol, ''), 'XAUUSD'), coalesce(p_net, 0))
    on conflict (client_id, deal_id) do nothing;

    get diagnostics inserted_rows = row_count;
    if inserted_rows = 0 then
        select weight, sample_count into updated_weight, updated_samples
        from public.alzool_engine_live_model_weights
        where client_id = p_client_id and strategy_id = p_strategy_id;
        return jsonb_build_object('updated', false, 'weight', coalesce(updated_weight, 1.0),
                                  'sample_count', coalesce(updated_samples, 0));
    end if;

    insert into public.alzool_engine_live_model_weights
        (client_id, strategy_id, weight, sample_count, wins, losses, total_net, last_updated)
    values
        (p_client_id, p_strategy_id,
         greatest(0.5, least(1.5, 1.0 + case when coalesce(p_net,0) > 0 then 0.02
                                             when coalesce(p_net,0) < 0 then -0.02 else 0 end)),
         1,
         case when coalesce(p_net,0) > 0 then 1 else 0 end,
         case when coalesce(p_net,0) < 0 then 1 else 0 end,
         coalesce(p_net,0), now())
    on conflict (client_id, strategy_id) do update set
        weight = greatest(0.5, least(1.5,
            public.alzool_engine_live_model_weights.weight +
            case when excluded.total_net > 0 then 0.02
                 when excluded.total_net < 0 then -0.02 else 0 end)),
        sample_count = public.alzool_engine_live_model_weights.sample_count + 1,
        wins = public.alzool_engine_live_model_weights.wins + excluded.wins,
        losses = public.alzool_engine_live_model_weights.losses + excluded.losses,
        total_net = public.alzool_engine_live_model_weights.total_net + excluded.total_net,
        last_updated = now()
    returning weight, sample_count into updated_weight, updated_samples;

    return jsonb_build_object('updated', true, 'weight', updated_weight,
                              'sample_count', updated_samples);
end;
$$;

revoke all on function public.alzool_apply_live_learning(text, text, integer, text, numeric) from public, anon, authenticated;
grant execute on function public.alzool_apply_live_learning(text, text, integer, text, numeric) to service_role;
