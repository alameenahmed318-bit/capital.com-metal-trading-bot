-- Durable MT5 position-to-strategy mapping for live learning.
-- The MT5 exit deal comment may be empty and Render memory is ephemeral.
create table if not exists public.alzool_engine_position_strategies (
  client_id text not null,
  position_id text not null,
  strategy_id integer not null check (strategy_id between 1 and 200),
  symbol text not null default '',
  magic text not null default '',
  opened_at timestamptz,
  source text not null default 'mt5_position_comment',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (client_id, position_id)
);
create index if not exists alzool_engine_position_strategies_updated_idx
  on public.alzool_engine_position_strategies (client_id, updated_at desc);
alter table public.alzool_engine_position_strategies enable row level security;
revoke all on table public.alzool_engine_position_strategies from public, anon, authenticated;
grant select, insert, update, delete on table public.alzool_engine_position_strategies to service_role;
