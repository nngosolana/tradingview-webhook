import logging
from typing import Dict

import pandas as pd
from binance.um_futures import UMFutures
from config import (DISCORD_WEBHOOK_URL)
from discord_webhook import DiscordWebhook

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
