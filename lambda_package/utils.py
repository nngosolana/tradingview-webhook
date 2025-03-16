import logging
from typing import Dict, Tuple

import pandas as pd
from binance.um_futures import UMFutures
from config import DISCORD_WEBHOOK_URL
from discord_webhook import DiscordWebhook
from models import SignalData

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _send_discord_notification(message: str):
    try:
        webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=message)
        response = webhook.execute()
        if response.status_code != 204:
            logger.error(f"Failed to send Discord notification: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error sending Discord notification: {str(e)}")


def calculate_macd(client: UMFutures, interval_raw: str, limit: int, symbol: str,
                   fast_length: int = 18, slow_length: int = 39, signal_length: int = 15) -> Dict:
    try:
        interval_map = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "D": "1d"}
        interval = interval_map.get(str(interval_raw), "1m")
        klines = client.klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                           'quote_asset_volume', 'trades', 'taker_buy_base', 'taker_buy_quote',
                                           'ignored'])
        df['close'] = pd.to_numeric(df['close'])
        ema_fast = df['close'].ewm(span=fast_length, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow_length, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_length, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            "macd": macd_line.iloc[-1],
            "signal": signal_line.iloc[-1],
            "histogram": histogram.iloc[-1],
            "prev_histogram": histogram.iloc[-2]
        }
    except Exception as e:
        logger.error(f"Error calculating MACD for {symbol}: {str(e)}")
        return {"macd": None, "signal": None, "histogram": None, "prev_histogram": None}


def score_signal(client: UMFutures, data: SignalData, position_type: str) -> Tuple[int, dict]:
    scores = {
        "Trend Tracer": 0,
        "Trend Strength": 0,
        "Smart Trail": 0,
        "Reversal Zones": 0,
        "Price Action": 0
    }
    close = data.close_price
    open_price = data.open_price
    volume = data.volume

    if data.trend_tracer is not None:
        if position_type == "LONG" and close > data.trend_tracer:
            scores["Trend Tracer"] = 15
        elif position_type == "SHORT" and close < data.trend_tracer:
            scores["Trend Tracer"] = 15

    if data.smart_trail is not None and data.neo_lead is not None and data.neo_lag is not None:
        neo_trend = data.neo_lead > data.neo_lag if position_type == "LONG" else data.neo_lead < data.neo_lag
        if neo_trend:
            scores["Trend Strength"] = 15

    if data.smart_trail is not None:
        if position_type == "LONG" and close > data.smart_trail:
            scores["Smart Trail"] = 20
        elif position_type == "SHORT" and close < data.smart_trail:
            scores["Smart Trail"] = 20

    if position_type == "LONG" and data.rz_s1 is not None and abs((close - data.rz_s1) / data.rz_s1) <= 0.01:
        scores["Reversal Zones"] = 10
    elif position_type == "SHORT" and data.rz_r1 is not None and abs((close - data.rz_r1) / data.rz_r1) <= 0.01:
        scores["Reversal Zones"] = 10

    if position_type == "LONG" and close > open_price and volume > 0:
        scores["Price Action"] = 10
    elif position_type == "SHORT" and close < open_price and volume > 0:
        scores["Price Action"] = 10

    total_score = sum(scores.values())
    logger.info(f"Signal Score for {data.symbol} ({position_type}): {total_score}/100 - {scores}")
    return min(total_score, 100), scores
