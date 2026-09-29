import json
import math
import os
import re
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from capital_api import CapitalAPI
from portfolio_risk import portfolio_risk_overlay
from execution_costs import evaluate_pretrade_cost
import ai_engine
import ai_pipeline
import ai_outcomes
import capital_news
import professional_ai_monitor
from capital_websocket import CapitalLivePriceStream

DEMO_ONLY = True
STRATEGY_ID = "CAPITAL_FX_AI"
POSITION_OWNERSHIP_FILE = "fx_ai_strategy_positions.json"
LEGACY_POSITION_OWNERSHIP_FILE = "strategy_positions.json"
ALLOW_GRID = False
ALLOW_MARTINGALE = False
ALLOW_AVERAGING = False
# 25/9 entry inversion requested for demo testing: strategy direction is
# intentionally flipped only at order execution. Position management is normal.
REVERSE_ENTRY_DIRECTION = True
# FX wrapper enables this to discover all tradeable currency markets returned
# by Capital.com. Other bots leave it disabled.
DYNAMIC_FX_UNIVERSE = False

STRONG_SIGNAL_MIN_CONFIDENCE = 0.80
GRID_STEP_R = 0.75
MARTINGALE_MULTIPLIER = 1.25
AGGRESSIVE_BASE_RISK = getattr(config, "RISK_PER_TRADE", 0.01)
MAX_BASKET_RISK = 0.04

EPICS = list(dict.fromkeys(getattr(config, "EPICS", ["GOLD", "EURUSD", "SILVER", "OIL_CRUDE", "US100", "US500"])))