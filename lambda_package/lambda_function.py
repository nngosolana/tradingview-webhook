import json
import logging
import uuid
from typing import Optional, Tuple

from utils import calculate_macd, _send_discord_notification
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
        logger.info("START: extract_event_data")
        try:
            logger.info(f"Raw event: {event}")
            if not isinstance(event, dict):
                raise ValueError("Event must be a dictionary")
            body = event.get('body')
            if body is None:
                raise ValueError("Event is missing 'body' field")
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

        if "Bullish Confirmation" in alert and "Exit" not in alert:
            position_type = "LONG"
            signal_type = "position_trigger"
        elif "Bearish Confirmation" in alert and "Exit" not in alert:
            position_type = "SHORT"
            signal_type = "position_trigger"
        elif "Bullish Confirmation" in alert:
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

    def score_signal(self, data: SignalData, position_type: str) -> Tuple[int, dict]:
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
                scores["Trend Tracer"] = 30
            elif position_type == "SHORT" and close < data.trend_tracer:
                scores["Trend Tracer"] = 30
            elif abs((close - data.trend_tracer) / data.trend_tracer) <= 0.005:
                scores["Trend Tracer"] = 15

        if data.smart_trail is not None and data.neo_lead is not None and data.neo_lag is not None:
            trail_distance = abs((close - data.smart_trail) / data.smart_trail)
            neo_trend = data.neo_lead > data.neo_lag if position_type == "LONG" else data.neo_lead < data.neo_lag
            if trail_distance > 0.01 and neo_trend:
                scores["Trend Strength"] = 25
            elif trail_distance > 0 and neo_trend:
                scores["Trend Strength"] = 15
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
                scores["Reversal Zones"] = 15
            elif abs((close - data.rz_s1) / data.rz_s1) <= 0.01:
                scores["Reversal Zones"] = 8
        elif position_type == "SHORT" and data.rz_r1 is not None:
            if abs((close - data.rz_r1) / data.rz_r1) <= 0.005:
                scores["Reversal Zones"] = 15
            elif abs((close - data.rz_r1) / data.rz_r1) <= 0.01:
                scores["Reversal Zones"] = 8

        if position_type == "LONG" and close > open_price:
            scores["Price Action"] += 5
            if volume > 0:
                scores["Price Action"] += 5
        elif position_type == "SHORT" and close < open_price:
            scores["Price Action"] += 5
            if volume > 0:
                scores["Price Action"] += 5

        total_score = sum(scores.values())
        logger.info(f"Signal Score for {data.symbol} ({position_type}): {total_score}/100 - {scores}")
        return min(total_score, 100), scores

    def process_signal(self, data: SignalData) -> dict:
        logger.info("START: process_signal")
        message = 'Webhook received successfully, no trade executed'
        position_type, signal_type, value = self.detect_position_type(data.alert)
        logger.info(f"Received Signal - Alert: {data.alert}, Symbol: {data.symbol}, Position: {position_type}, "
                    f"Signal Type: {signal_type}, Price: {data.close_price}, TP1: {data.tp1}, SL1: {data.sl1}")

        if signal_type == "position_trigger":
            signal_score, score_components = self.score_signal(data, position_type)
            logger.info(f"Signal Score: {signal_score}/100 - Components: {score_components}")
            signal_uuid = str(uuid.uuid4())  # Generate unique UUID for this signal

            # Shorten alert if too long
            alert_short = data.alert[:10] + "+" if len(data.alert) > 10 else data.alert

            if signal_score < SIGNAL_SCORE_THRESHOLD:
                message = f"Signal rejected - Score {signal_score}/100 too low"
                logger.info(message)
                discord_msg = (
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
                _send_discord_notification(discord_msg)
            else:
                existing_position = self._check_existing_position(data.symbol, position_type)
                discord_msg = (
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
                if existing_position:
                    message = f"{position_type} SETUP - Position Exists, Updating SL/TP (Score: {signal_score})"
                    logger.info(message)
                    _send_discord_notification(discord_msg)
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
                            message = f"{position_type} SETUP - SL/TP Updated Successfully (Score: {signal_score})"
                            logger.info(message)
                else:
                    message = f"{position_type} SETUP - Trade Triggered (Score: {signal_score})"
                    logger.info(message)
                    _send_discord_notification(discord_msg)
                    if not ENABLE_MACD_CHECK or self.verify_macd_signal(data.symbol, data.interval_raw, position_type):
                        logger.info(
                            f"TRIGGER {position_type} SETUP - MACD check {'disabled' if not ENABLE_MACD_CHECK else 'passed'}")
                        clear_result = handle_order_logic("close_all_symbol_orders", data.symbol)
                        if clear_result.get("status") == "error":
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
                            logger.error(message)

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
            "body": json.dumps({
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
            })
        }


def lambda_handler(event: dict, context: object) -> dict:
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
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }


def main():
    logger.info("START: main")
    test_input = {
        "alert": "Bearish Confirmation",
        "interval": "1",
        "ticker": "XRPUSDT",
        "exchange": "BINANCE",
        "sector": "na",
        "market": "Crypto",
        "tf": "1m",
        "bartime": "1741741081000",
        "year": "2025",
        "month": "3",
        "day": "12",
        "ohlcv": {
            "open": 2.29180,
            "high": 2.29180,
            "low": 2.28830,
            "close": 2.28830,
            "volume": 107502.0
        },
        "indicators": {
            "smart_trail": 2.29780,
            "rz_r3": 2.31550,
            "rz_r2": 2.30870,
            "rz_r1": 2.30200,
            "rz_s1": 2.28350,
            "rz_s2": 2.27680,
            "rz_s3": 2.27000,
            "catcher": 2.28920,
            "tracer": 2.28920,
            "neo_lead": 2.29050,
            "neo_lag": 2.28270,
            "tp1": 2.27680,
            "sl1": 2.29930,
            "tp2": 2.26630,
            "sl2": 2.30870
        }
    }
    event = {"body": json.dumps(test_input)}
    response = lambda_handler(event, None)
    print("Response:")
    print(json.dumps(response, indent=2))
    logger.info("END: main")


if __name__ == "__main__":
    main()
