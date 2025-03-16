import json
import logging

from binance_trade_wrapper import get_binance_client
from lambda_function import lambda_handler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_current_price(symbol: str = "DOGEUSDT") -> float:
    """Fetch the current price of the given symbol from Binance."""
    try:
        client = get_binance_client()
        ticker = client.ticker_price(symbol=symbol)
        current_price = float(ticker["price"])
        logger.info(f"Current price of {symbol}: {current_price}")
        return current_price
    except Exception as e:
        logger.error(f"Failed to fetch current price for {symbol}: {str(e)}")
        return 0.17  # Fallback price if fetch fails


def generate_sample_signals(symbol: str = "DOGEUSDT"):
    """Generate sample signals with current price for testing."""
    current_price = fetch_current_price(symbol)

    # Base OHLCV and indicators with realistic offsets
    base_ohlcv = {
        "open": current_price * 1.005,  # 0.5% above current
        "close": current_price,
        "volume": 1000000.0
    }
    base_indicators = {
        "tp1": current_price * 0.99,  # TP1 1% below current
        "sl1": current_price * 1.02,  # SL1 2% above current
        "tp2": current_price * 0.97,  # TP2 3% below current
        "sl2": current_price * 1.04  # SL2 4% above current
    }

    # Sample signals
    samples = [
        # TP1 Reach (Bullish)
        {
            "alert": "TP1 Reached Bullish",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy()
        },
        # TP2 Reach (Bearish)
        {
            "alert": "TP2 Reached Bearish",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy(),
            "running": "False"  # Set running to "True
        },
        # Bullish Confirmation
        {
            "alert": "Bullish Confirmation",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy(),
            "running": "False"  # Set running to "True
        },
        # Bearish Confirmation
        {
            "alert": "Bearish Confirmation",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy(),
            "running": "False"  # Set running to "True
        },
        # Bullish Exit
        {
            "alert": "Bullish Exit",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy(),
            "running": "False"  # Set running to "True
        },
        # Bearish Exit
        {
            "alert": "Bearish Exit",
            "ticker": symbol,
            "tf": "1h",
            "ohlcv": base_ohlcv.copy(),
            "indicators": base_indicators.copy(),
            "running": "False"  # Set running to "True
        }
    ]
    return samples


def test_lambda_locally():
    """Run local tests for Lambda handler with sample signals."""
    logger.info("START: test_lambda_locally")

    # Generate sample signals
    sample_signals = generate_sample_signals()

    # Simulate Lambda execution for each sample
    for sample in sample_signals:
        event = {"body": json.dumps(sample)}
        context = None  # Context is not used in this local test

        logger.info(f"Testing signal: {sample['alert']}")
        response = lambda_handler(event, context)

        # Log the response
        logger.info(f"Response for {sample['alert']}:\n{json.dumps(response, indent=2)}")
        print(f"\nResponse for {sample['alert']}:\n{json.dumps(response, indent=2)}")


if __name__ == "__main__":
    test_lambda_locally()
