import json
import logging
from typing import Optional, Tuple

from algorithm import calculate_macd
from binance_trade_wrapper import get_binance_client
from order_processor import handle_order_logic
from config import INVESTMENT_PERCENTAGE, LEVERAGE, MACD_DELTA_RATIO, ENABLE_MACD_CHECK, SIGNAL_SCORE_THRESHOLD

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SignalData:
    """Class to store parsed signal data as attributes."""

    def __init__(self, data: dict):
        self.alert = data.get('alert', '')
        self.symbol = data.get('ticker', '')
        self.exchange = data.get('exchange', '')
        self.sector = data.get('sector', 'na')
        self.market = data.get('market', 'Crypto')
        self.interval_raw = data.get('interval', '1')
        self.tf = data.get('tf', '')
        self.bartime = data.get('bartime', '')
        self.year = data.get('year', '')
        self.month = data.get('month', '')
        self.day = data.get('day', '')

        ohlcv = data.get('ohlcv', {})
        self.open_price = float(ohlcv.get('open', 0)) if ohlcv.get('open') is not None else 0.0
        self.high_price = float(ohlcv.get('high', 0)) if ohlcv.get('high') is not None else 0.0
        self.low_price = float(ohlcv.get('low', 0)) if ohlcv.get('low') is not None else 0.0
        self.close_price = float(ohlcv.get('close', 0)) if ohlcv.get('close') is not None else 0.0
        self.volume = float(ohlcv.get('volume', 0)) if ohlcv.get('volume') is not None else 0.0

        indicators = data.get('indicators', {})
        self.smart_trail = float(indicators.get('smart_trail', 0)) if indicators.get(
            'smart_trail') is not None else None
        self.rz_r3 = float(indicators.get('rz_r3', 0)) if indicators.get('rz_r3') is not None else None
        self.rz_r2 = float(indicators.get('rz_r2', 0)) if indicators.get('rz_r2') is not None else None
        self.rz_r1 = float(indicators.get('rz_r1', 0)) if indicators.get('rz_r1') is not None else None
        self.rz_s1 = float(indicators.get('rz_s1', 0)) if indicators.get('rz_s1') is not None else None
        self.rz_s2 = float(indicators.get('rz_s2', 0)) if indicators.get('rz_s2') is not None else None
        self.rz_s3 = float(indicators.get('rz_s3', 0)) if indicators.get('rz_s3') is not None else None
        self.trend_catcher = float(indicators.get('catcher', 0)) if indicators.get('catcher') is not None else None
        self.trend_tracer = float(indicators.get('tracer', 0)) if indicators.get('tracer') is not None else None
        self.neo_lead = float(indicators.get('neo_lead', 0)) if indicators.get('neo_lead') is not None else None
        self.neo_lag = float(indicators.get('neo_lag', 0)) if indicators.get('neo_lag') is not None else None
        self.tp1 = float(indicators.get('tp1', 0)) if indicators.get('tp1') is not None else None
        self.sl1 = float(indicators.get('sl1', 0)) if indicators.get('sl1') is not None else None
        self.tp2 = float(indicators.get('tp2', 0)) if indicators.get('tp2') is not None else None
        self.sl2 = float(indicators.get('sl2', 0)) if indicators.get('sl2') is not None else None


class TradingSignalProcessor:
    """Class to process trading signals from webhook events."""

    def __init__(self):
        self.client = get_binance_client()
        if not self.client:
            raise Exception("Failed to initialize Binance client")

    def extract_event_data(self, event) -> SignalData:
        """Extract signal data from the event's 'body' field and parse it."""
        logger.info("START: extract_event_data")
        try:
            # Log the raw event for debugging
            logger.info(f"Raw event: {event}")

            # Expect event to be a dict from API Gateway with a 'body' field
            if not isinstance(event, dict):
                raise ValueError("Event must be a dictionary")

            # Extract the 'body' field, which contains the signal data as a string
            body = event.get('body')
            if body is None:
                raise ValueError("Event is missing 'body' field")

            # Parse the body string into a dictionary
            if isinstance(body, str):
                body = json.loads(body)
            elif not isinstance(body, dict):
                raise ValueError("Body must be a string (JSON) or dictionary")

            signal_data = SignalData(body)
            logger.info(f"Extracted data: {vars(signal_data)}")
            return signal_data
        except Exception as e:
            logger.error(f"Failed to parse event: {str(e)}")
            raise

    def detect_position_type(self, alert: str) -> Tuple[Optional[str], str, Optional[float]]:
        logger.info(f"START: detect_position_type - Alert: {alert}")
        position_type = None
        signal_type = None
        value = None

        parts = alert.split()
        for part in parts:
            try:
                value = float(part)
                break
            except ValueError:
                continue

        # Prioritize Confirmation over Exit
        if "Bullish Confirmation" in alert and "Exit" not in alert:
            position_type = "LONG"
            signal_type = "position_trigger"
        elif "Bearish Confirmation" in alert and "Exit" not in alert:
            position_type = "SHORT"
            signal_type = "position_trigger"
        elif "Bullish Confirmation" in alert:  # New condition for mixed signals
            position_type = "LONG"
            signal_type = "position_trigger"
        elif "Bearish Confirmation" in alert:
            position_type = "SHORT"
            signal_type = "position_trigger"
        elif "Bullish Exit" in alert:
            position_type = "LONG"
            signal_type = "position_exit"
        elif "Bearish Exit" in alert:
            position_type = "SHORT"
            signal_type = "position_exit"
        elif "TP1" in alert and "Reached" in alert:
            signal_type = "tp_reach"
        elif "TP2" in alert and "Reached" in alert:
            signal_type = "tp_reach"
        elif "SL1" in alert and "Reached" in alert:
            signal_type = "sl_reach"
        elif "SL2" in alert and "Reached" in alert:
            signal_type = "sl_reach"

        logger.info(f"Detected - Position: {position_type}, Signal Type: {signal_type}, Value: {value}")
        return position_type, signal_type, value

    def _check_existing_position(self, symbol: str, position_type: str) -> Optional[dict]:
        """Check if a position of the given type exists for the symbol."""
        logger.info(f"Checking existing position for {symbol}, type: {position_type}")
        position_info = self.client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)

        if position:
            current_position_type = "LONG" if float(position["positionAmt"]) > 0 else "SHORT"
            logger.info(f"Found position: {current_position_type}, Quantity: {position['positionAmt']}")
            if current_position_type == position_type:
                return position
            return None
        logger.info(f"No {position_type} position found for {symbol}")
        return None

    def verify_macd_signal(self, symbol: str, interval_raw: str, position_type: str) -> bool:
        """Verify MACD conditions and return True if the position is good to proceed."""
        logger.info(f"START: verify_macd_signal - Symbol: {symbol}, Position: {position_type}")
        macd_result = calculate_macd(client=self.client, symbol=symbol, interval_raw=interval_raw, limit=500)

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

    def score_signal(self, data: SignalData, position_type: str) -> int:
        """Score the signal quality from 0 to 100 based on available data."""
        score = 0
        close = data.close_price
        open_price = data.open_price
        volume = data.volume

        # 1. Trend Tracer Alignment (30 points)
        if data.trend_tracer is not None:
            if position_type == "LONG" and close > data.trend_tracer:
                score += 30
            elif position_type == "SHORT" and close < data.trend_tracer:
                score += 30
            elif abs((close - data.trend_tracer) / data.trend_tracer) <= 0.005:  # Within 0.5%
                score += 15

        # 2. Trend Strength Approximation (25 points)
        if data.smart_trail is not None and data.neo_lead is not None and data.neo_lag is not None:
            trail_distance = abs((close - data.smart_trail) / data.smart_trail)
            neo_trend = data.neo_lead > data.neo_lag if position_type == "LONG" else data.neo_lead < data.neo_lag
            if trail_distance > 0.01 and neo_trend:  # >1% from smart_trail
                score += 25
            elif trail_distance > 0 and neo_trend:
                score += 15
            elif trail_distance <= 0.005:  # Within 0.5%
                score += 5

        # 3. Smart Trail Confluence (20 points)
        if data.smart_trail is not None:
            if position_type == "LONG" and close > data.smart_trail:
                score += 20
            elif position_type == "SHORT" and close < data.smart_trail:
                score += 20
            elif abs((close - data.smart_trail) / data.smart_trail) <= 0.005:  # Within 0.5%
                score += 10

        # 4. Reversal Zones Confluence (15 points)
        if position_type == "LONG" and data.rz_s1 is not None:
            if abs((close - data.rz_s1) / data.rz_s1) <= 0.005:
                score += 15
            elif abs((close - data.rz_s1) / data.rz_s1) <= 0.01:
                score += 8
        elif position_type == "SHORT" and data.rz_r1 is not None:
            if abs((close - data.rz_r1) / data.rz_r1) <= 0.005:
                score += 15
            elif abs((close - data.rz_r1) / data.rz_r1) <= 0.01:
                score += 8

        # 5. Price Action Strength (10 points)
        if position_type == "LONG" and close > open_price:
            score += 5
            if volume > 0:  # Basic volume check
                score += 5
        elif position_type == "SHORT" and close < open_price:
            score += 5
            if volume > 0:
                score += 5

        logger.info(f"Signal Score for {data.symbol} ({position_type}): {score}/100")
        return min(score, 100)  # Cap at 100
    def process_signal(self, data: SignalData) -> dict:
        """Process the trading signal and return response."""
        logger.info("START: process_signal")
        message = 'Webhook received successfully, no trade executed'

        position_type, signal_type, value = self.detect_position_type(data.alert)
        logger.info(f"Received Signal - Alert: {data.alert}, Symbol: {data.symbol}, Position: {position_type}, "
                    f"Signal Type: {signal_type}, Price: {data.close_price}, TP1: {data.tp1}, SL1: {data.sl1}")
        signal_score = self.score_signal(data, position_type)
        logger.info(f"Signal Score: {signal_score}/100")

        if signal_type == "position_trigger":
            existing_position = self._check_existing_position(data.symbol, position_type)

            if existing_position:
                message = f"{position_type} SETUP - Position Exists, Updating SL/TP"
                logger.info(message)

                clear_result = handle_order_logic("clear_orders", data.symbol)
                if clear_result is not True:
                    message = f"{position_type} SETUP - Failed to clear orders: {clear_result.get('message', 'Unknown error')}"
                    logger.error(message)
                else:
                    quantity = abs(float(existing_position["positionAmt"]))
                    sl_result = handle_order_logic("place_stop_loss", data.symbol, position_type=position_type,
                                                   stop_loss_price=data.sl1, quantity=quantity)
                    tp_result = handle_order_logic("place_take_profit", data.symbol, position_type=position_type,
                                                   take_profit_price=data.tp2, quantity=quantity)
                    if sl_result.get("status") == "error" or tp_result.get("status") == "error":
                        message = f"{position_type} SETUP - Failed to update SL/TP: {sl_result.get('message', '')} {tp_result.get('message', '')}"
                        logger.error(message)
                    else:
                        message = f"{position_type} SETUP - SL/TP Updated Successfully"
                        logger.info(message)
            else:
                message = f"{position_type} SETUP - Trade Triggered"
                logger.info(message)
                if not ENABLE_MACD_CHECK or self.verify_macd_signal(data.symbol, data.interval_raw, position_type):
                    logger.info(
                        f"TRIGGER {position_type} SETUP - MACD check {'disabled' if not ENABLE_MACD_CHECK else 'passed'}")
                    clear_result = handle_order_logic("close_all_symbol_orders", data.symbol)
                    if "error" in str(clear_result.get("status", "")):
                        message = f"{position_type} SETUP - Failed to clear existing orders: {clear_result.get('message', 'Unknown error')}"
                        logger.error(message)
                    else:
                        if signal_score < SIGNAL_SCORE_THRESHOLD:  # Adjustable threshold
                            message = f"Signal rejected - Score {signal_score}/100 too low"
                            logger.info(message)
                        else:
                            result = handle_order_logic(
                                f"open_{position_type.lower()}_sl_tp_without_investment",
                                data.symbol,
                                stop_loss_price=data.sl1,
                                take_profit_price=data.tp2,
                                investment_percentage=INVESTMENT_PERCENTAGE,
                                leverage=LEVERAGE
                            )
                            if result.get("status") == "error":
                                message = f"{position_type} SETUP - Failed: {result.get('message', 'Unknown error')}"
                                logger.error(message)
                else:
                    logger.info("MACD conditions not met, closing all positions for safety")
                    message = "CLOSE ALL POSITIONS - Safety Triggered"
                    result = handle_order_logic("close_all_symbol_orders", data.symbol)
                    if result.get("status") == "error":
                        message = f"Close Position - Failed: {result.get('message', 'Unknown error')}"

        elif signal_type == "position_exit":
            logger.info("TRIGGER EXIT SETUP")
            message = "EXIT SETUP - Partial Take Profit Triggered"
            result = handle_order_logic("take_profit_partially", data.symbol, leverage=LEVERAGE)
            if result.get("status") == "error":
                message = f"EXIT SETUP - Failed: {result.get('message', 'Unknown error')}"

        elif signal_type == "tp_reach":
            logger.info(f"TP Reached - Value: {value}")
            message = f"TAKE PROFIT REACHED - Value: {value}, Taking Partial Profit"
            result = handle_order_logic("take_profit_partially", data.symbol, leverage=LEVERAGE)
            if result.get("status") == "error":
                message = f"TP REACH - Failed: {result.get('message', 'Unknown error')}"

        elif signal_type == "sl_reach":
            logger.info(f"SL Reached - Value: {value}")
            message = f"STOP LOSS REACHED - Value: {value}, Closing All Positions"
            result = handle_order_logic("close_all_symbol_orders", data.symbol)
            if result.get("status") == "error":
                message = f"SL REACH - Failed: {result.get('message', 'Unknown error')}"

        return {
            "statusCode": 200,
            "body": {
                "message": message,
                "interval": data.tf,
                "signal": {
                    "alert": data.alert,
                    "symbol": data.symbol,
                    "position_type": position_type,
                    "close_price": data.close_price,
                    "take_profit": data.tp2,
                    "stop_loss": data.sl1,
                    "signal_score": signal_score if signal_type == "position_trigger" else None
                }
            }
        }


def lambda_handler(event: dict, context: object) -> dict:
    """AWS Lambda handler function."""
    logger.info("START: lambda_handler")
    try:
        processor = TradingSignalProcessor()
        data = processor.extract_event_data(event)
        response = processor.process_signal(data)
        logger.info(f"Response: {response}")
        return response
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            "statusCode": 500,
            "body": {"error": f"Internal server error: {str(e)}"}
        }


def main():
    """Main function for local testing."""
    logger.info("START: main")
    test_input = {
        "alert": "Bullish Confirmation",
        "interval": "1",
        "ticker": "ETHUSDT",
        "exchange": "BINANCE",
        "sector": "na",
        "market": "Crypto",
        "tf": "1",
        "bartime": "1741741081000",
        "year": "2025",
        "month": "3",
        "day": "12",
        "ohlcv": {
            "open": "0.16317", "high": "0.16319", "low": "0.16299", "close": "0.16299", "volume": "196461"
        },
        "indicators": {
            "smart_trail": "0.1644178133377382", "rz_r3": "0.1669824463710614", "rz_r2": "0.166080965595846",
            "rz_r1": "0.1651794848206306", "rz_s1": "0.1624179780031806", "rz_s2": "0.1615164972279652",
            "rz_s3": "0.1606150164527498", "catcher": "0.16346498717", "tracer": "0.163700210962177",
            "neo_lead": "0.1638974899860026", "neo_lag": "0.1620825100139974", "tp1": "0.1620825100139974",
            "sl1": "0.1638974899860026", "tp2": "0.161184029238782", "sl2": "0.164798970761218"
        }
    }
    event = {"body": test_input}
    response = lambda_handler(event, None)
    print("Response:")
    from json import dumps
    print(dumps(response, indent=2))
    logger.info("END: main")


if __name__ == "__main__":
    main()
