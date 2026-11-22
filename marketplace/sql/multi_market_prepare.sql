-- STAGING PREPARATION ONLY. Review and apply against the Meeno database
-- AFTER integration tests and explicit pilot setup; do not run on old Gold DB.
-- Keep every existing BTC identity and credential intact. Gold gets a new
-- connection identity under the same authenticated owner, created by pairing.
begin;
alter table public.alzool_engine_clients add column if not exists market text not null default 'BTCUSD';
alter table public.alzool_engine_clients add constraint alzool_engine_clients_market_check check (market in ('BTCUSD','XAUUSD'));
alter table public.alzool_engine_clients drop constraint alzool_engine_clients_user_id_key;
alter table public.alzool_engine_clients add constraint alzool_engine_clients_user_market_key unique(user_id,market);
alter table public.alzool_engine_clients add column if not exists account_login text;
alter table public.alzool_engine_clients add column if not exists broker_server text;
alter table public.alzool_engine_clients add constraint alzool_engine_clients_account_binding_check check ((account_login is null) = (broker_server is null));
alter table public.alzool_engine_clients add constraint alzool_engine_clients_distinct_broker_account unique(user_id,broker_server,account_login);
alter table public.alzool_engine_clients enable row level security;
alter table public.alzool_engine_trade_settings enable row level security;
grant select,insert,update,delete on public.alzool_engine_trade_settings to service_role;
commit;
-- No historic trades/settings are copied across new identity boundaries.
-- Both markets must be paired to their own dedicated account; new gold
-- connection keeps trading disabled until the owner explicitly enables it.
