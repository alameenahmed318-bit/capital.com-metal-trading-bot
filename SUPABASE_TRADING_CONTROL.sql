-- AL-ZOOL remote trading permission
-- Run once in Supabase SQL Editor before deploying/using dashboard trading controls.
ALTER TABLE public.alzool_engine_clients
ADD COLUMN IF NOT EXISTS trading_enabled boolean NOT NULL DEFAULT false;

-- Safety: existing accounts remain stopped until the owner explicitly enables them.
UPDATE public.alzool_engine_clients
SET trading_enabled = false
WHERE trading_enabled IS NULL;
