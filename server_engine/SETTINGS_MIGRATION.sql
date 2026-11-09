-- AL-ZOOL settings migration: preserve legacy EA values and current central-client values.
-- Apply once in Supabase SQL Editor.
alter table public.alzool_engine_clients
  add column if not exists trading_settings jsonb not null default
  '{
    "start_lot":0.01,
    "lot_multiplier":1.5,
    "cap_martingale_lot":true,
    "max_lot_cap":5.0,
    "grid_distance_points":1400,
    "max_grid_depth":14,
    "volatility_tier_active":true,
    "step_quiet":1200,
    "step_volatile":1750,
    "enable_visual_alerts":true,
    "enable_sound_alerts":true,
    "alert_sound_file":"alert.wav",
    "magic_number":101101,
    "show_az_dashboard":true,
    "single_profit_target":null,
    "basket_profit_target":null,
    "daily_loss_limit":null,
    "stop_loss_atr":1.8,
    "take_profit_atr":1.2,
    "trailing_enabled":true,
    "trail_start_atr":1.0,
    "trail_distance_atr":0.8,
    "smart_loss_guard":null,
    "cooling_enabled":null,
    "cooling_distance_points":null,
    "strategy_mode":"AUTO",
    "selected_strategy_id":null
  }'::jsonb;
