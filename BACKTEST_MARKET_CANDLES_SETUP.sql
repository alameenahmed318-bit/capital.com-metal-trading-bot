-- AL-ZOOL historical market candles for the Backtest Lab.
-- RLS is enabled and only the server-side service_role receives table access.
create table if not exists public.alzool_engine_market_candles (
  id bigint generated always as identity primary key,
  client_id text not null references public.alzool_engine_clients(client_id) on delete cascade,
  symbol text not null,
  timeframe text not null,
  candle_time timestamptz not null,
  candle_open numeric(20,8) not null,
  candle_high numeric(20,8) not null,
  candle_low numeric(20,8) not null,
  candle_close numeric(20,8) not null,
  tick_volume bigint not null default 0,
  spread_points integer not null default 0,
  point_size numeric(20,10) not null default 0,
  tick_size numeric(20,10) not null default 0,
  tick_value numeric(20,10) not null default 0,
  contract_size numeric(20,8) not null default 0,
  digits smallint not null default 2,
  account_currency text not null default '',
  uploaded_at timestamptz not null default now(),
  constraint alzool_engine_market_candles_unique unique (client_id,symbol,timeframe,candle_time),
  constraint alzool_engine_market_candles_ohlc check (
    candle_high >= candle_low and candle_high >= candle_open and candle_high >= candle_close
    and candle_low <= candle_open and candle_low <= candle_close
  )
);
create index if not exists alzool_engine_market_candles_lookup
  on public.alzool_engine_market_candles (client_id,symbol,timeframe,candle_time desc);
alter table public.alzool_engine_market_candles enable row level security;
revoke all on public.alzool_engine_market_candles from anon, authenticated;
grant select, insert, update on public.alzool_engine_market_candles to service_role;
grant usage, select on sequence public.alzool_engine_market_candles_id_seq to service_role;
