-- AL-ZOOL central engine durable storage
-- Run once in Supabase SQL Editor before enabling the engine gateway.

create table if not exists public.alzool_engine_clients (
  client_id text primary key,
  user_id uuid not null unique references auth.users(id) on delete cascade,
  token_hash text not null,
  expires_at timestamptz not null,
  revoked boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.alzool_engine_decisions (
  client_id text primary key references public.alzool_engine_clients(client_id) on delete cascade,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.alzool_engine_weights (
  client_id text not null references public.alzool_engine_clients(client_id) on delete cascade,
  strategy_id integer not null check (strategy_id between 1 and 200),
  weight double precision not null default 1.0 check (weight between 0.25 and 4.0),
  primary key (client_id, strategy_id)
);

create table if not exists public.alzool_engine_outcomes (
  client_id text not null references public.alzool_engine_clients(client_id) on delete cascade,
  deal_id text not null,
  strategy_id integer not null check (strategy_id between 1 and 200),
  pnl double precision not null,
  created_at timestamptz not null default now(),
  primary key (client_id, deal_id)
);

alter table public.alzool_engine_clients enable row level security;
alter table public.alzool_engine_decisions enable row level security;
alter table public.alzool_engine_weights enable row level security;
alter table public.alzool_engine_outcomes enable row level security;

-- No client-side policies: access is server-only through the service-role key.
revoke all on public.alzool_engine_clients from anon, authenticated;
revoke all on public.alzool_engine_decisions from anon, authenticated;
revoke all on public.alzool_engine_weights from anon, authenticated;
revoke all on public.alzool_engine_outcomes from anon, authenticated;

grant all on public.alzool_engine_clients to service_role;
grant all on public.alzool_engine_decisions to service_role;
grant all on public.alzool_engine_weights to service_role;
grant all on public.alzool_engine_outcomes to service_role;
