-- AL-ZOOL remote trading settings migration
-- Run once in Supabase SQL Editor. All risk-increasing features default OFF.
alter table public.alzool_engine_clients
  add column if not exists trading_settings jsonb not null default
  '{"lots":0.01,"max_positions":1,"single_profit_target":0.0,"basket_profit_target":0.0,"daily_loss_limit":0.0,"stop_loss_atr":1.8,"take_profit_atr":1.2,"trailing_enabled":true,"trailing_start_atr":1.0,"trailing_distance_atr":0.8,"smart_loss_guard":false,"reinforcement_enabled":false,"cooling_enabled":false,"lot_multiplier":1.0,"reinforcement_distance_points":1400,"cooling_distance_points":1400,"max_additional_positions":0,"strategy_mode":"AUTO","selected_strategy_id":0}'::jsonb;

alter table public.alzool_engine_clients enable row level security;
revoke all on public.alzool_engine_clients from anon, authenticated;
grant all on public.alzool_engine_clients to service_role;
