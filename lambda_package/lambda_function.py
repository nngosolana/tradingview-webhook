import json
import logging
import uuid
from typing import Optional, Tuple

from binance_trade_wrapper import get_binance_client, fetch_all_positions
from config import INVESTMENT_PERCENTAGE, LEVERAGE
from models import SignalData
from order_processor import create_order_with_sl_tp, close_position, take_profit_partially, place_stop_loss_order
from price_calculation_processor import calculate_params_with_sl_tp_without_invest_percentage
from utils import _send_discord_notification, score_signal

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TradingSignalProcessor:
    def __init__(self):
        self.client = get_binance_client()
        if not self.client:
            raise Exception("Failed to initialize Binance client")

    def extract_event_data(self, event) -> SignalData:
        logger.info("START: extract_event_data")
        try:
            body = event.get('body')
            if isinstance(body, str):
                body = json.loads(body)
            signal_data = SignalData(body)
            logger.info(f"Extracted data: {vars(signal_data)}")
            return signal_data
        except Exception as e:
            logger.error(f"Failed to parse event: {str(e)}")
            raise

    def detect_position_type(self, alert: str, tp1: float, tp2: float) -> Tuple[Optional[str], str]:
        logger.info(f"START: detect_position_type - Alert: {alert}")
        position_type = None
        signal_type = None

        if "Bullish Confirmation" in alert:
            position_type = "LONG"
            signal_type = "confirmation"
        elif "Bearish Confirmation" in alert:
            position_type = "SHORT"
            signal_type = "confirmation"
        elif "Bullish Exit" in alert:
            position_type = "LONG"
            signal_type = "exit"
        elif "Bearish Exit" in alert:
            position_type = "SHORT"
            signal_type = "exit"
        elif "TP1" in alert and "Reached" in alert:
            signal_type = "tp1_reach"
            position_type = "SHORT" if tp1 > tp2 else "LONG"
        elif "TP2" in alert and "Reached" in alert:
            signal_type = "tp2_reach"
            position_type = "SHORT" if tp1 > tp2 else "LONG"
        elif "SL1" in alert and "Reached" in alert or "SL2" in alert and "Reached" in alert:
            signal_type = "sl_reach"
            position_type = "LONG" if tp2 < tp1 else "SHORT"

        logger.info(f"Detected - Position: {position_type}, Signal Type: {signal_type}")
        return position_type, signal_type

    def calculate_new_tp(self, current_price: float, stop_loss_price: float, position_type: str,
                         risk_reward: float = 6.0) -> float:
        distance = abs(current_price - stop_loss_price)
        if position_type == "LONG":
            return current_price + risk_reward * distance
        return current_price - risk_reward * distance

    def adjust_sl_for_exit(self, current_price: float, entry_price: float, current_sl: float,
                           position_type: str) -> float:
        mid_point = (current_price + entry_price) / 2
        if position_type == "LONG":
            new_sl = max(current_sl, mid_point) if current_sl < mid_point else current_sl
            return max(new_sl, entry_price) if new_sl < entry_price else new_sl
        new_sl = min(current_sl, mid_point) if current_sl > mid_point else current_sl
        return min(new_sl, entry_price) if new_sl > entry_price else new_sl

    def process_signal(self, data: SignalData) -> dict:
        logger.info("START: process_signal")
        position_type, signal_type = self.detect_position_type(data.alert, data.tp1, data.tp2)
        positions = fetch_all_positions(self.client, data.symbol)
        existing_position = next((pos for pos in positions if pos.position_type == position_type), None)
        opposite_position = next((pos for pos in positions if pos.position_type != position_type), None)
        signal_uuid = str(uuid.uuid4())
        message = "Signal processed"
        action_result = {}

        if signal_type == "confirmation":
            if opposite_position:
                action_result = close_position(self.client, data.symbol, opposite_position.position_type, LEVERAGE)
                message = f"Closed opposite {opposite_position.position_type} position"
            elif existing_position:
                message = f"{position_type} position exists, no action taken"
            else:
                message = "No position exists, awaiting TP1"

        elif signal_type == "tp1_reach":
            signal_score, _ = score_signal(self.client, data, position_type)
            investment_adj = (signal_score / 100) * INVESTMENT_PERCENTAGE
            new_tp = self.calculate_new_tp(data.close_price, data.sl2, position_type)

            if existing_position:
                message = f"{position_type} position exists, ignoring TP1"
            else:
                calc_result = calculate_params_with_sl_tp_without_invest_percentage(
                    self.client, data.symbol, position_type, data.sl2, new_tp, investment_adj, LEVERAGE
                )
                if "status" in calc_result and calc_result["status"] == "error":
                    logger.error(f"Calculation failed: {calc_result['message']}")
                    action_result = calc_result
                else:
                    if opposite_position:
                        action_result = close_position(self.client, data.symbol, opposite_position.position_type,
                                                       LEVERAGE)
                        if action_result.get("status") != "error":
                            action_result = create_order_with_sl_tp(
                                self.client, data.symbol, position_type, calc_result["stop_loss_price"],
                                calc_result["take_profit_price"], calc_result["quantity"],
                                calc_result["investment_amount"], calc_result["market_price"], LEVERAGE
                            )
                            message = f"Closed {opposite_position.position_type}, opened {position_type}"
                    else:
                        action_result = create_order_with_sl_tp(
                            self.client, data.symbol, position_type, calc_result["stop_loss_price"],
                            calc_result["take_profit_price"], calc_result["quantity"],
                            calc_result["investment_amount"], calc_result["market_price"], LEVERAGE
                        )
                        message = f"Opened new {position_type} position"

        elif signal_type == "tp2_reach" and existing_position:
            new_tp = self.calculate_new_tp(data.close_price, data.sl1, position_type)
            action_result = take_profit_partially(
                self.client, data.symbol, LEVERAGE, new_tp, abs(float(existing_position.positionAmt) * 0.4)
            )
            if action_result.get("status") != "error":
                action_result = place_stop_loss_order(
                    self.client, data.symbol, position_type, data.sl1, action_result["remaining_quantity"]
                )
                if action_result:
                    action_result = {"status": "success", "stop_loss_order": action_result}
                else:
                    action_result = {"status": "error", "message": "Failed to place stop loss"}
                message = f"Adjusted SL to SL1, new TP set for {position_type}"

        elif signal_type == "exit" and existing_position:
            current_sl = float(data.sl1)  # Assuming this as current SL
            new_sl = self.adjust_sl_for_exit(data.close_price, float(existing_position.entryPrice), current_sl,
                                             position_type)
            new_tp = self.calculate_new_tp(data.close_price, new_sl, position_type)
            action_result = take_profit_partially(
                self.client, data.symbol, LEVERAGE, new_tp, abs(float(existing_position.positionAmt) * 0.4)
            )
            if action_result.get("status") != "error":
                action_result = place_stop_loss_order(
                    self.client, data.symbol, position_type, new_sl, action_result["remaining_quantity"]
                )
                if action_result:
                    action_result = {"status": "success", "stop_loss_order": action_result}
                else:
                    action_result = {"status": "error", "message": "Failed to place stop loss"}
                message = f"Partial TP (40%) taken, SL adjusted for {position_type}"

        elif signal_type == "sl_reach" and existing_position:
            message = f"SL reached for {position_type}, handled externally"

        # Prepare and send Discord message
        discord_table = (
            f"**Signal Processed - {data.symbol} ({signal_type}) [UUID: {signal_uuid}]**\n"
            f"```\n"
            f"{'Field':<15} | {'Value':<15} | {'Field':<15} | {'Value':<15}\n"
            f"{'-' * 15}+{'-' * 15}+{'-' * 15}+{'-' * 15}\n"
            f"{'Position':<15} | {position_type or 'N/A':<15} | {'Message':<15} | {message:<15}\n"
            f"{'Close Price':<15} | {data.close_price:<15.5f} | {'TP1':<15} | {data.tp1:<15.5f}\n"
            f"{'SL1':<15} | {data.sl1:<15.5f} | {'TP2':<15} | {data.tp2:<15.5f}\n"
            f"{'SL2':<15} | {data.sl2:<15.5f} | {'Score':<15} | {locals().get('signal_score', 'N/A'):<15}\n"
            f"{'Investment %':<15} | {locals().get('investment_adj', INVESTMENT_PERCENTAGE):<15.2f} | {'Leverage':<15} | {LEVERAGE:<15}\n"
            f"```\n"
        )
        _send_discord_notification(discord_table)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "signal": {
                    "alert": data.alert,
                    "symbol": data.symbol,
                    "position_type": position_type,
                    "close_price": data.close_price,
                    "take_profit": data.tp2,
                    "stop_loss": data.sl1,
                    "signal_score": locals().get("signal_score")
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
        _send_discord_notification(f"**ERROR**: Internal server error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }
