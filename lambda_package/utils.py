import logging
from typing import Dict, Tuple

import pandas as pd
from binance.um_futures import UMFutures
from config import INVESTMENT_PERCENTAGE, LEVERAGE, ENABLE_MACD_CHECK, \
    SIGNAL_SCORE_THRESHOLD, DISCORD_WEBHOOK_URL, MACD_DELTA_RATIO  # Ensure LEVERAGE is imported
from discord_webhook import DiscordWebhook
from models import SignalData, Position

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _send_discord_notification(message: str):
    """Send a message to Discord via webhook."""
    try:
        webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=message)
        response = webhook.execute()
        if response.status_code == 204:
            logger.info("Discord notification sent successfully")
        else:
            logger.error(f"Failed to send Discord notification: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error sending Discord notification: {str(e)}")


def calculate_macd(client: UMFutures,
                   interval_raw: str,
                   limit: int,
                   symbol: str,
                   fast_length: int = 18,
                   slow_length: int = 39,
                   signal_length: int = 15) -> Dict:
    """Calculate MACD with given parameters using provided klines data."""
    try:

        logging.info(f"START: get_klines - symbol: {symbol}, interval: {interval_raw}, limit: {limit}")
        interval_map = {
            "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
            "60": "1h", "120": "2h", "240": "4h", "D": "1d", "1D": "1d"
        }
        interval = interval_map.get(str(interval_raw), "1m")
        logging.info(f"Mapped interval '{interval_raw}' to Binance interval '{interval}'")

        klines = client.klines(symbol=symbol, interval=interval, limit=limit)
        logging.info(f"END: get_klines - Retrieved {len(klines)} klines")

        # Validate klines input
        if not klines or len(klines) < 2:
            raise ValueError(f"Insufficient klines data for {symbol}: {len(klines)} rows, need at least 2")

        # Convert klines to DataFrame
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignored'
        ])

        # Check if 'close' column exists and has valid data
        if 'close' not in df.columns or df['close'].isna().all():
            raise ValueError(f"No valid 'close' price data in klines for {symbol}")

        logging.info(f"Latest Close Price for {symbol}: {df['close'].iloc[-1]}")

        # Convert 'close' to float, handling potential non-numeric values
        try:
            df['close'] = pd.to_numeric(df['close'], errors='raise')
        except ValueError as e:
            raise ValueError(f"Failed to convert 'close' prices to float for {symbol}: {str(e)}")

        # Calculate MACD
        ema_fast = df['close'].ewm(span=fast_length, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow_length, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_length, adjust=False).mean()
        histogram = macd_line - signal_line

        # Get latest values with safety checks
        latest_macd = macd_line.iloc[-1]
        latest_signal = signal_line.iloc[-1]
        latest_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]  # Safe since we checked len(klines) >= 2

        logging.info(f"MACD Calculated for {symbol} - MACD: {latest_macd}, Signal: {latest_signal}, "
                     f"Histogram: {latest_histogram}, Prev Histogram: {prev_histogram}")
        return {
            "macd": latest_macd,
            "signal": latest_signal,
            "histogram": latest_histogram,
            "prev_histogram": prev_histogram
        }
    except Exception as e:
        logging.error(f"Error calculating MACD for {symbol}: {str(e)}")
        return {"macd": None, "signal": None, "histogram": None, "prev_histogram": None}


def verify_macd_signal(client: UMFutures, symbol: str, interval_raw: str, position_type: str) -> bool:
    logger.info(f"START: verify_macd_signal - Symbol: {symbol}, Position: {position_type}")
    macd_result = calculate_macd(client=client, symbol=symbol, interval_raw=interval_raw, limit=500)
    if not macd_result.get('histogram'):
        logger.error("MACD calculation failed")
        raise Exception("MACD calculation failed")
    latest_histogram = macd_result['histogram']
    prev_histogram = macd_result['prev_histogram']
    swing = (latest_histogram * prev_histogram < 0) if prev_histogram != 0 else False
    ratio_condition_met = False
    if not swing and prev_histogram != 0:
        if position_type == "LONG":
            if latest_histogram < 0 and prev_histogram < 0:
                ratio_condition_met = abs(latest_histogram) < MACD_DELTA_RATIO * abs(prev_histogram)
            elif latest_histogram > 0 and prev_histogram > 0:
                ratio_condition_met = abs(prev_histogram) < MACD_DELTA_RATIO * abs(latest_histogram)
        elif position_type == "SHORT":
            if latest_histogram < 0 and prev_histogram < 0:
                ratio_condition_met = abs(latest_histogram) > MACD_DELTA_RATIO * abs(prev_histogram)
            elif latest_histogram > 0 and prev_histogram > 0:
                ratio_condition_met = abs(prev_histogram) > MACD_DELTA_RATIO * abs(latest_histogram)
    logger.info(f"MACD Results - Latest: {latest_histogram}, Prev: {prev_histogram}, Swing: {swing}, "
                f"Ratio Condition Met: {ratio_condition_met}")
    return swing or ratio_condition_met


def score_signal(client: UMFutures, data: SignalData, position_type: str) -> Tuple[int, dict]:
    """Score the signal and return total score plus individual components."""
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
        elif abs((close - data.trend_tracer) / data.trend_tracer) <= 0.005:
            scores["Trend Tracer"] = 5

    if data.smart_trail is not None and data.neo_lead is not None and data.neo_lag is not None:
        trail_distance = abs((close - data.smart_trail) / data.smart_trail)
        neo_trend = data.neo_lead > data.neo_lag if position_type == "LONG" else data.neo_lead < data.neo_lag
        if trail_distance > 0.01 and neo_trend:
            scores["Trend Strength"] = 15
        elif trail_distance > 0 and neo_trend:
            scores["Trend Strength"] = 10
        elif trail_distance <= 0.005:
            scores["Trend Strength"] = 5

    if data.smart_trail is not None:
        if position_type == "LONG" and close > data.smart_trail:
            scores["Smart Trail"] = 20
        elif position_type == "SHORT" and close < data.smart_trail:
            scores["Smart Trail"] = 20
        elif abs((close - data.smart_trail) / data.smart_trail) <= 0.005:
            scores["Smart Trail"] = 10

    if position_type == "LONG" and data.rz_s1 is not None:
        if abs((close - data.rz_s1) / data.rz_s1) <= 0.005:
            scores["Reversal Zones"] = 10
        elif abs((close - data.rz_s1) / data.rz_s1) <= 0.01:
            scores["Reversal Zones"] = 5
    elif position_type == "SHORT" and data.rz_r1 is not None:
        if abs((close - data.rz_r1) / data.rz_r1) <= 0.005:
            scores["Reversal Zones"] = 10
        elif abs((close - data.rz_r1) / data.rz_r1) <= 0.01:
            scores["Reversal Zones"] = 5

    if position_type == "LONG" and close > open_price:
        scores["Price Action"] += 5
        if volume > 0:
            scores["Price Action"] += 5
    elif position_type == "SHORT" and close < open_price:
        scores["Price Action"] += 5
        if volume > 0:
            scores["Price Action"] += 5

    if not ENABLE_MACD_CHECK or verify_macd_signal(client, data.symbol, data.interval_raw, position_type):
        scores["MACD"] = 30

    total_score = sum(scores.values())
    logger.info(f"Signal Score for {data.symbol} ({position_type}): {total_score}/100 - {scores}")
    return min(total_score, 100), scores

def create_rejected_signal_message(data: SignalData, position_type: str, signal_score: int,
                                   score_components: dict, signal_uuid: str) -> str:
    """Create Discord message for rejected signal."""
    alert_short = data.alert[:10] + "+" if len(data.alert) > 10 else data.alert
    return (
        f"**Signal Rejected - {data.symbol} ({position_type}) [UUID: {signal_uuid}]**\n"
        f"**Trade Data**\n"
        f"```\n"
        f"{'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11}\n"
        f"{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}\n"
        f"{'Total Score':<15} | {f'{signal_score}/100':<11} | {'Stop Loss':<15} | {f'{data.sl1:.5f}':<11} | {'Smart Trail':<15} | {f'{data.smart_trail:.5f}':<11} | {'RZ S1':<15} | {f'{data.rz_s1:.5f}':<11}\n"
        f"{'Alert':<15} | {alert_short:<11} | {'Open Price':<15} | {f'{data.open_price:.5f}':<11} | {'Neo Lead':<15} | {f'{data.neo_lead:.5f}':<11} | {'RZ R1':<15} | {f'{data.rz_r1:.5f}':<11}\n"
        f"{'Close Price':<15} | {f'{data.close_price:.5f}':<11} | {'Trend Tracer':<15} | {f'{data.trend_tracer:.5f}':<11} | {'Neo Lag':<15} | {f'{data.neo_lag:.5f}':<11} | {'Volume':<15} | {f'{data.volume}':<11}\n"
        f"{'Threshold':<15} | {f'{SIGNAL_SCORE_THRESHOLD}':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11}\n"
        f"```\n"
        f"**Scoring Components**\n"
        f"```\n"
        f"{'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11}\n"
        f"{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}\n"
        f"{'Trend Tracer':<15} | {f'{score_components['Trend Tracer']}':<11} | {'Trend Strength':<15} | {f'{score_components['Trend Strength']}':<11} | {'Smart Trail':<15} | {f'{score_components['Smart Trail']}':<11} | {'Reversal Zones':<15} | {f'{score_components['Reversal Zones']}':<11}\n"
        f"{'Price Action':<15} | {f'{score_components['Price Action']}':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11}\n"
        f"```\n"
    )

def create_order_started_message(data: SignalData, position_type: str, signal_score: int,
                                 score_components: dict, signal_uuid: str) -> str:
    """Create Discord message for order started."""
    alert_short = data.alert[:10] + "+" if len(data.alert) > 10 else data.alert
    return (
        f"**Order Started - {data.symbol} ({position_type}) [UUID: {signal_uuid}]**\n"
        f"**Trade Data**\n"
        f"```\n"
        f"{'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11}\n"
        f"{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}\n"
        f"{'Total Score':<15} | {f'{signal_score}/100':<11} | {'Stop Loss':<15} | {f'{data.sl1:.5f}':<11} | {'Smart Trail':<15} | {f'{data.smart_trail:.5f}':<11} | {'RZ S1':<15} | {f'{data.rz_s1:.5f}':<11}\n"
        f"{'Alert':<15} | {alert_short:<11} | {'Open Price':<15} | {f'{data.open_price:.5f}':<11} | {'Neo Lead':<15} | {f'{data.neo_lead:.5f}':<11} | {'RZ R1':<15} | {f'{data.rz_r1:.5f}':<11}\n"
        f"{'Close Price':<15} | {f'{data.close_price:.5f}':<11} | {'Trend Tracer':<15} | {f'{data.trend_tracer:.5f}':<11} | {'Neo Lag':<15} | {f'{data.neo_lag:.5f}':<11} | {'Volume':<15} | {f'{data.volume}':<11}\n"
        f"{'Take Profit':<15} | {f'{data.tp2:.5f}':<11} | {'Leverage':<15} | {f'{LEVERAGE}x':<11} | {'Investment %':<15} | {f'{INVESTMENT_PERCENTAGE}':<11} | {'':<15} | {'':<11}\n"
        f"```\n"
        f"**Scoring Components**\n"
        f"```\n"
        f"{'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11} | {'Field':<15} | {'Value':<11}\n"
        f"{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}-+-{'-' * 15}-+-{'-' * 11}\n"
        f"{'Trend Tracer':<15} | {f'{score_components['Trend Tracer']}':<11} | {'Trend Strength':<15} | {f'{score_components['Trend Strength']}':<11} | {'Smart Trail':<15} | {f'{score_components['Smart Trail']}':<11} | {'Reversal Zones':<15} | {f'{score_components['Reversal Zones']}':<11}\n"
        f"{'Price Action':<15} | {f'{score_components['Price Action']}':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11} | {'':<15} | {'':<11}\n"
        f"```\n"
    )