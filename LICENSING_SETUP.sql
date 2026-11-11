-- AL-ZOOL licensing table only. Existing customer rows must be preserved.
create table if not exists public.alzool_engine_clients (
  client_id text primary key,
  user_id uuid not null unique references auth.users(id) on delete cascade,
  token_hash text not null,
  expires_at timestamptz not null,
  revoked boolean not null default false,
  created_at timestamptz not null default now()
);
alter table public.alzool_engine_clients enable row level security;
revoke all on public.alzool_engine_clients from anon, authenticated;
grant all on public.alzool_engine_clients to service_role;
