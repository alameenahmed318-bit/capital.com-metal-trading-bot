
def ai_manage_positions(api, positions, epic, ai_decision):
    """AI position manager: HOLD / PROTECT / EXIT using the final AI market state.

    Broker SL/TP and trailing remain safety nets. The AI does not close simply
    because a fixed profit amount was reached; it closes when the market thesis
    materially changes or the validated expected edge turns negative.
    """
    if not ai_decision or not ai_decision.get("enabled"):
        return

    advanced = ai_decision.get("advanced_ai") if isinstance(ai_decision.get("advanced_ai"), dict) else {}
    signal = advanced.get("signal_after") or ai_decision.get("signal") or ai_decision.get("raw_signal")
    confidence = float(ai_decision.get("confidence", 0.0) or 0.0)
    pm = advanced.get("position_management") if isinstance(advanced.get("position_management"), dict) else {}
    action_bias = str(pm.get("action_bias") or "WAIT").upper()
    explicit_exit = bool(
        ai_decision.get("exit")
        or ai_decision.get("should_exit")
        or advanced.get("exit")
        or advanced.get("should_exit")
    )
    exit_confidence = max(0.65, float(os.environ.get("AI_EXIT_CONFIDENCE", "0.65")))

    for position in get_positions_for_epic(positions, epic):
        direction = position_direction(position)
        deal_id = position_deal_id(position)
        pnl = position_unrealized_pnl(position)
        if not deal_id:
            continue

        opposite_signal = (
            signal in ("BUY", "SELL")
            and direction in ("BUY", "SELL")
            and signal != direction
        )
        reversal_exit = opposite_signal and confidence >= exit_confidence
        negative_edge_exit = (
            action_bias == "EXIT_NEGATIVE_EDGE"
            and signal == direction
            and confidence >= exit_confidence
        )
        ai_authorized_exit = explicit_exit or reversal_exit or negative_edge_exit

        if ai_authorized_exit:
            try:
                response = api.close_position(deal_id)
                if explicit_exit:
                    reason = "EXPLICIT_AI_EXIT"
                elif reversal_exit:
                    reason = "AI_REVERSAL"
                else:
                    reason = "AI_NEGATIVE_EXPECTED_EDGE"
                log(
                    f"{epic}: AI EXIT | reason={reason} | existing={direction} | "
                    f"AI={signal} | action={action_bias} | confidence={confidence:.3f} | "
                    f"pnl={pnl:.2f} | deal={deal_id} | response={response}"
                )
            except Exception as exc:
                log(f"{epic}: AI EXIT failed | deal={deal_id} | {exc}")
            continue

        if action_bias == "PROTECT":
            log(
                f"{epic}: AI PROTECT | existing={direction} | AI={signal} | "
                f"confidence={confidence:.3f} | pnl={pnl:.2f} | "
                f"uncertainty={advanced.get('enhanced_uncertainty')} | "
                f"regime={(advanced.get('regime') or {}).get('regime', 'UNKNOWN')}"
            )
        elif action_bias == "HOLD" and direction == signal:
            log(
                f"{epic}: AI HOLD | existing={direction} | confidence={confidence:.3f} | "
                f"pnl={pnl:.2f} | regime={(advanced.get('regime') or {}).get('regime', 'UNKNOWN')}"
            )

def manage_profit_trailing(api, positions, epic, account_currency):
    """Lock profit after +20 account-currency units; allow an 8-unit pullback."""