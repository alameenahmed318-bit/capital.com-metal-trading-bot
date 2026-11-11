"""Official OpenAI Responses API market-analysis adapter for AL-ZOOL.

This module is analysis-only. It never creates broker commands or places trades.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "market_regime": {"type": "string", "enum": ["TREND", "RANGE", "VOLATILE", "BREAKOUT", "UNCLEAR"]},
        "bias": {"type": "string", "enum": ["BUY", "SELL", "WAIT"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary_ar": {"type": "string"},
        "reasons_ar": {"type": "array", "items": {"type": "string"}},
        "risks_ar": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["market_regime", "bias", "confidence", "summary_ar", "reasons_ar", "risks_ar"],
}

SYSTEM_INSTRUCTIONS = (
    "أنت محلل أسواق محترف يعمل ضمن AL-ZOOL. حلّل فقط البيانات الفعلية المرسلة لك. "
    "لا تخترع أسعاراً أو مؤشرات أو أخباراً أو بيانات غير موجودة. إذا كانت البيانات ناقصة أو متضاربة "
    "فاختر bias=WAIT وmarket_regime=UNCLEAR وثقة منخفضة. قدّم رأياً تحليلياً احتماليّاً لا وعداً بالربح. "
    "لا تصدر أوامر تداول، ولا تقترح تجاوز إعدادات المستخدم، ولا تغيّر اتجاه التعافي. "
    "اكتب الملخص والأسباب والمخاطر بالعربية وباختصار."
)


class OpenAIAnalysisError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _output_text(response: dict) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                value = part.get("text")
                if isinstance(value, str):
                    chunks.append(value)
            elif isinstance(part, dict) and part.get("type") == "refusal":
                raise OpenAIAnalysisError("model_refusal")
    if not chunks:
        raise OpenAIAnalysisError("empty_model_response")
    return "".join(chunks)


def _validate_result(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(OUTPUT_SCHEMA["required"]):
        raise OpenAIAnalysisError("invalid_output_schema")
    if value["market_regime"] not in OUTPUT_SCHEMA["properties"]["market_regime"]["enum"]:
        raise OpenAIAnalysisError("invalid_market_regime")
    if value["bias"] not in ("BUY", "SELL", "WAIT"):
        raise OpenAIAnalysisError("invalid_bias")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
        raise OpenAIAnalysisError("invalid_confidence")
    if not isinstance(value["summary_ar"], str) or not value["summary_ar"].strip():
        raise OpenAIAnalysisError("invalid_summary")
    for key in ("reasons_ar", "risks_ar"):
        if not isinstance(value[key], list) or any(not isinstance(x, str) for x in value[key]):
            raise OpenAIAnalysisError("invalid_explanations")
        value[key] = [x[:500] for x in value[key][:8]]
    value["summary_ar"] = value["summary_ar"][:1200]
    value["confidence"] = float(confidence)
    return value


def analyze_market(api_key: str, model: str, market_context: dict, *, timeout: float = 12.0, opener=None) -> dict:
    """Call the official Responses API and return a strictly validated analysis."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise OpenAIAnalysisError("api_key_not_configured")
    if not isinstance(market_context, dict):
        raise OpenAIAnalysisError("invalid_market_context")
    request_body = {
        "model": (model or "gpt-6-astra").strip(),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(market_context, ensure_ascii=False, separators=(",", ":"))}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "alzool_market_analysis",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            }
        },
        "max_output_tokens": 500,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key.strip(), "Content-Type": "application/json"},
        method="POST",
    )
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OpenAIAnalysisError("api_http_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OpenAIAnalysisError("api_connection_error") from None
    try:
        response_data = json.loads(raw.decode("utf-8"))
        if response_data.get("status") == "incomplete":
            raise OpenAIAnalysisError("incomplete_model_response")
        parsed = json.loads(_output_text(response_data))
    except OpenAIAnalysisError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
        raise OpenAIAnalysisError("invalid_model_response") from None
    return _validate_result(parsed)
