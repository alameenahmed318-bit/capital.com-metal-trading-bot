-- Owner-only local export. SELECT only. Never commit the export itself.
-- Exact symbol/client/bar, original server reception time; never join future signals.
WITH outcomes AS (
  SELECT DISTINCT ON (client_id, payload->>'symbol', (payload->>'bar_time')::bigint)
    client_id, payload::jsonb AS p,
    payload->>'symbol' AS symbol,
    (payload->>'bar_time')::bigint AS bar,
    (payload->>'observed_at')::double precision AS obs
  FROM public.alzool_engine_signal_events
  WHERE event_type = 'MASTER_V3_OUTCOME'
    AND payload#>'{outcomes,60,payoffs}' IS NOT NULL
    AND (payload->>'observed_at')::numeric-(payload->>'bar_time')::numeric BETWEEN 60 AND 120
    AND (payload#>>'{outcomes,60,quote_time}')::numeric>(payload->>'observed_at')::numeric
  ORDER BY client_id, payload->>'symbol', (payload->>'bar_time')::bigint,
    (payload->>'observed_at')::numeric
), archive AS (
  SELECT client_id, event_type, payload::jsonb AS evidence,
    payload->>'symbol' AS symbol, (payload->>'bar_time')::bigint AS bar,
    (payload->>'received_at')::double precision AS received
  FROM public.alzool_engine_signal_events
  WHERE event_type IN ('ARENA_LIVE_FORECAST','PARALLEL_META_FORECAST')
    AND payload->>'status'='SHADOW_READY'
)
SELECT o.client_id, o.p || jsonb_build_object(
  'arena_evidence', coalesce(a.evidence,'{}'::jsonb),
  'parallel_evidence', coalesce(s.evidence,'{}'::jsonb)) AS payload
FROM outcomes o
LEFT JOIN LATERAL (
  SELECT evidence FROM archive a
  WHERE a.event_type='ARENA_LIVE_FORECAST' AND a.client_id=o.client_id
    AND a.symbol=o.symbol AND a.bar=o.bar
    AND a.received BETWEEN o.obs-60 AND o.obs
  ORDER BY a.received DESC LIMIT 1
) a ON true
LEFT JOIN LATERAL (
  SELECT evidence FROM archive s
  WHERE s.event_type='PARALLEL_META_FORECAST' AND s.client_id=o.client_id
    AND s.symbol=o.symbol AND s.bar=o.bar
    AND s.received BETWEEN o.obs-60 AND o.obs
  ORDER BY s.received DESC LIMIT 1
) s ON true
ORDER BY o.symbol,o.bar;
