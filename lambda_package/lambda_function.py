import json
import logging
from typing import Optional, Tuple

from algorithm import calculate_macd
from binance_trade_wrapper import get_binance_client
from order_processor import handle_order_logic
from config import INVESTMENT_PERCENTAGE, LEVERAGE, MACD_DELTA_RATIO, ENABLE_MACD_CHECK

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SignalData:
    """Class to store parsed signal data as attributes."""

    def __init__(self, event: dict):
        body = event.get('body', event)

        # Symbol data
        self.alert = body.get('alert', '')
        self.symbol = body.get('ticker', '')
        self.exchange = body.get('exchange', '')
        self.sector = body.get('sector', 'na')
        self.market = body.get('market', 'Crypto')
        self.interval_raw = body.get('interval', '1')
        self.tf = body.get('tf', '')
        self.bartime = body.get('bartime', '')
        self.year = body.get('year', '')
        self.month = body.get('month', '')
        self.day = body.get('day', '')

        # OHLCV data
        ohlcv = body.get('ohlcv', {})
        self.open_price = float(ohlcv.get('open', 0)) if ohlcv.get('open') is not None else 0.0
        self.high_price = float(ohlcv.get('high', 0)) if ohlcv.get('high') is not None else 0.0
        self.low_price = float(ohlcv.get('low', 0)) if ohlcv.get('low') is not None else 0.0
        self.close_price = float(ohlcv.get('close', 0)) if ohlcv.get('close') is not None else 0.0
        self.volume = float(ohlcv.get('volume', 0)) if ohlcv.get('volume') is not None else 0.0

        # Indicators
        indicators = body.get('indicators', {})
        self.smart_trail = float(indicators.get('smart_trail', 0)) if indicators.get(
            'smart_trail') is not None else None
        self.rz_r3 = float(indicators.get('rz_r3', 0)) if indicators.get('rz_r3') is not None else None
        self.rz_r2 = float(indicators.get('rz_r2', 0)) if indicators.get('rz_r2') is not None else None
        self.rz_r1 = float(indicators.get('rz_r1', 0)) if indicators.get('rz_r1') is not None else None
        self.rz_s1 = float(indicators.get('rz_s1', 0)) if indicators.get('rz_s1') is not None else None
        self.rz_s2 = float(indicators.get('rz_s2', 0)) if indicators.get('rz_s2') is not None else None
        self.rz_s3 = float(indicators.get('rz_s3', 0)) if indicators.get('rz_s3') is not None else None
        self.catcher = float(indicators.get('catcher', 0)) if indicators.get('catcher') is not None else None
        self.tracer = float(indicators.get('tracer', 0)) if indicators.get('tracer') is not None else None
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
        logger.info("START: extract_event_data")
        logger.info(f"Event: {event}")
        try:
            # If event is a string, parse it as JSON
            if isinstance(event, str):
                event = json.loads(event)
            elif not isinstance(event, dict):
                raise ValueError("Event must be a string (JSON) or dictionary")

            signal_data = SignalData(event)
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

    def process_signal(self, data: SignalData) -> dict:
        """Process the trading signal and return response."""
        logger.info("START: process_signal")
        message = 'Webhook received successfully, no trade executed'

        position_type, signal_type, value = self.detect_position_type(data.alert)
        logger.info(f"Received Signal - Alert: {data.alert}, Symbol: {data.symbol}, Position: {position_type}, "
                    f"Signal Type: {signal_type}, Price: {data.close_price}, TP1: {data.tp1}, SL1: {data.sl1}")

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
                    "stop_loss": data.sl1
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
        "ticker": "DOGEUSDT",
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
