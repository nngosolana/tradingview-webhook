import json
import logging
import uuid
from typing import Optional, Tuple

from binance_trade_wrapper import get_binance_client, fetch_all_positions
from config import INVESTMENT_PERCENTAGE, LEVERAGE, ENABLE_MACD_CHECK, \
    SIGNAL_SCORE_THRESHOLD
from models import SignalData, Position
from order_processor import handle_order_logic
from utils import _send_discord_notification, score_signal, create_rejected_signal_message, create_order_started_message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

    def handle_existing_position(self, data: SignalData, position_type: str, signal_type: str, value: float,
                                 existing_position: Position, leverage: int) -> str:
        logger.info(f"START: handle_existing_position - Symbol: {data.symbol}, Position: {position_type}")
        discord_msg = (
            f"-------------------------EXISTING POSITION DETECTED---------------------------------\n"
            f"Symbol: {data.symbol} | Position: {position_type} | Signal: {signal_type}\n"
            f"Current Price: {data.close_price:.5f}"
        )
        _send_discord_notification(discord_msg)

        if signal_type == "position_trigger":
            message = f"{position_type} SETUP - Position Exists, Updating SL/TP"
            clear_result = handle_order_logic("update_new_sl_tp", data.symbol, exists_position=existing_position)
            if clear_result is not True:
                message = f"{position_type} SETUP - Failed to clear orders: {clear_result.get('message', 'Unknown error')}"
                logger.error(message)
                _send_discord_notification(f"**ERROR**: {message}")

        elif signal_type == "position_exit":
            logger.info("TRIGGER EXIT SETUP")
            message = "EXIT SETUP - Partial Take Profit Triggered"
            result = handle_order_logic("take_profit_partially", data.symbol, leverage=leverage,
                                        take_profit_price=data.tp2, signal_type=signal_type)
            if result.get("status") == "error":
                message = f"EXIT SETUP - Failed: {result.get('message', 'Unknown error')}"
                _send_discord_notification(f"**ERROR**: {message}")

        elif signal_type == "tp_reach":
            logger.info(f"TP Reached - Value: {value}")
            message = f"TAKE PROFIT REACHED - Value: {value}, Taking Partial Profit"
            result = handle_order_logic("take_profit_partially", data.symbol, leverage=leverage,
                                        take_profit_price=data.tp2, signal_type=signal_type)
            if result.get("status") == "error":
                message = f"TP REACH - Failed: {result.get('message', 'Unknown error')}"
                _send_discord_notification(f"**ERROR**: {message}")

        elif signal_type == "sl_reach":
            logger.info(f"SL Reached - Value: {value}")
            message = f"STOP LOSS REACHED - Value: {value}, Closing All Positions"
            result = handle_order_logic("close_all_symbol_orders", data.symbol)
            if result.get("status") == "error":
                message = f"SL REACH - Failed: {result.get('message', 'Unknown error')}"
                _send_discord_notification(f"**ERROR**: {message}")
            else:
                # Fetch PNL data for closed positions
                for pos_type, close_data in result.items():
                    if close_data.get("status") == "success" and close_data.get("pnl", 0) != 0:
                        discord_msg = (
                            f"-------------------------POSITION CLOSED---------------------------------\n"
                            f"Position Closed - {data.symbol} ({pos_type})\n"
                            f"PNL: {close_data['pnl']:.2f} USDT\n"
                            f"Investment: {close_data['investment']:.2f} USDT\n"
                            f"% Investment: {close_data['pnl_percent_investment']:.2f}%\n"
                            f"% Total Balance: {close_data['pnl_percent_balance']:.2f}%"
                        )
                        _send_discord_notification(discord_msg)

        return message

    def handle_new_position(self, data: SignalData, position_type: str, signal_type: str, value: float,
                            leverage: int) -> str:
        """Handle logic when no position exists."""
        logger.info(f"START: handle_new_position - Symbol: {data.symbol}, Position: {position_type}")
        discord_msg = (
            f"-------------------------NEW POSITION CHECK---------------------------------\n"
            f"Symbol: {data.symbol} | Position: {position_type} | Signal: {signal_type}\n"
            f"Current Price: {data.close_price:.5f}"
        )
        _send_discord_notification(discord_msg)

        if signal_type == "position_trigger":
            signal_score, score_components = score_signal(self.client, data, position_type)
            logger.info(f"Signal Score: {signal_score}/100 - Components: {score_components}")
            signal_uuid = str(uuid.uuid4())

            if signal_score < SIGNAL_SCORE_THRESHOLD:
                message = f"Signal rejected - Score {signal_score}/100 too low"
                logger.info(message)
                discord_msg = create_rejected_signal_message(data, position_type, signal_score, score_components,
                                                             signal_uuid)
                _send_discord_notification(discord_msg)
            else:
                discord_msg = create_order_started_message(data, position_type, signal_score, score_components,
                                                           signal_uuid)
                message = f"{position_type} SETUP - Trade Triggered (Score: {signal_score})"
                logger.info(message)
                _send_discord_notification(discord_msg)
                logger.info(
                    f"TRIGGER {position_type} SETUP - MACD check {'disabled' if not ENABLE_MACD_CHECK else 'passed'}")

                clear_result = handle_order_logic("close_all_symbol_orders", data.symbol)
                if clear_result.get("status") == "error":
                    message = f"{position_type} SETUP - Failed to clear existing orders: {clear_result.get('message', 'Unknown error')}"
                    logger.error(message)
                    _send_discord_notification(f"**ERROR**: {message}")
                else:
                    result = handle_order_logic(
                        f"open_{position_type.lower()}_sl_tp_without_investment",
                        data.symbol,
                        stop_loss_price=data.sl1,
                        take_profit_price=data.tp2,
                        investment_percentage=INVESTMENT_PERCENTAGE,
                        leverage=leverage
                    )
                    if result.get("status") == "error":
                        message = f"{position_type} SETUP - Failed: {result.get('message', 'Unknown error')}"
                        logger.error(message)
                        _send_discord_notification(f"**ERROR**: {message}")
                    else:
                        discord_msg = (
                            f"-------------------------NEW POSITION OPENED---------------------------------\n"
                            f"Position Opened - {data.symbol} ({position_type})\n"
                            f"Entry Price: {result['market_price']:.5f}\n"
                            f"Stop Loss: {result['calculated_sl']:.5f}\n"
                            f"Take Profit: {result['calculated_tp']:.5f}\n"
                            f"Quantity: {result['quantity']}\n"
                            f"Investment: {result['trade_amount']:.2f} USDT"
                        )
                        _send_discord_notification(discord_msg)

        elif signal_type in ["position_exit", "tp_reach", "sl_reach"]:
            logger.error("Position does not exist, ignoring signal")
            message = f"{position_type} EXIT - Position does not exist, ignoring signal"
            _send_discord_notification(f"**NOTICE**: {message}")

        return message

    def process_signal(self, data: SignalData) -> dict:
        """Process trading signal and orchestrate actions."""
        logger.info("START: process_signal")
        discord_msg = (
            f"-------------------------SIGNAL RECEIVED---------------------------------\n"
            f"Symbol: {data.symbol} | Alert: {data.alert}\n"
            f"Close Price: {data.close_price:.5f} | TP1: {data.tp1:.5f} | SL1: {data.sl1:.5f}"
        )
        _send_discord_notification(discord_msg)

        message = 'Webhook received successfully, no trade executed'
        position_type, signal_type, value = self.detect_position_type(data.alert)
        logger.info(f"Received Signal - Alert: {data.alert}, Symbol: {data.symbol}, Position: {position_type}, "
                    f"Signal Type: {signal_type}, Price: {data.close_price}, TP1: {data.tp1}, SL1: {data.sl1}")

        positions = fetch_all_positions(client=self.client, symbol=data.symbol)
        logger.info(f"Positions: {positions}")

        if len(positions) > 1:
            logger.error(f"Multiple positions found for {data.symbol}")
            _send_discord_notification(f"**ERROR**: Multiple positions found for {data.symbol}")

        exists_position = any(pos.position_type == position_type for pos in positions)
        if exists_position:
            existing_position = next((pos for pos in positions if pos.position_type == position_type), None)
            logger.info(f"Position already exists for {data.symbol}")
            message = self.handle_existing_position(data, position_type, signal_type, value, existing_position,
                                                    LEVERAGE)
        else:
            message = self.handle_new_position(data, position_type, signal_type, value, LEVERAGE)

        signal_score = locals().get('signal_score')  # Get signal_score if it was calculated
        discord_msg = (
            f"-------------------------SIGNAL PROCESSING COMPLETE---------------------------------\n"
            f"Symbol: {data.symbol} | Result: {message}"
        )
        _send_discord_notification(discord_msg)

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
        discord_msg = f"**ERROR**: Internal server error in lambda_handler: {str(e)}"
        _send_discord_notification(discord_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }


def main():
    logger.info("START: main")
    test_input = {
        "alert": "TP1 123 Reached",
        "interval": "1",
        "ticker": "LINKUSDT",
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
            "sl1": 2.5458,
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
