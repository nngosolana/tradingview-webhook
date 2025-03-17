import json
import logging
import uuid
from typing import Optional, Tuple

from binance_trade_wrapper import get_binance_client, fetch_all_positions
from config import INVESTMENT_PERCENTAGE, LEVERAGE
from models import SignalData
from order_processor import create_position_order, close_position, take_profit_partially, update_sl_tp_orders, \
    clear_all_symbol_orders
from price_calculation_processor import calculate_params_with_sl_tp_without_invest_percentage
from utils import _send_discord_notification, score_signal

# Set logging to DEBUG level
logging.basicConfig(
    level=logging.DEBUG,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TradingSignalProcessor:
    def __init__(self):
        logger.debug("START: __init__")
        self.client = get_binance_client()
        if not self.client:
            raise Exception("Failed to initialize Binance client")
        logger.debug("END: __init__ - client initialized")

    def extract_event_data(self, event) -> SignalData:
        logger.debug(f"START: extract_event_data - Input: event={event}")
        try:
            body = event.get('body')
            if isinstance(body, str):
                body = json.loads(body)
            signal_data = SignalData(body)
            logger.debug(f"END: extract_event_data - Output: signal_data={vars(signal_data)}")
            return signal_data
        except Exception as e:
            logger.error(f"Failed to parse event: {str(e)}")
            raise

    def detect_position_type(self, alert: str, tp1: float, tp2: float) -> Tuple[Optional[str], str]:
        logger.debug(f"START: detect_position_type - Input: alert={alert}, tp1={tp1}, tp2={tp2}")
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

        logger.debug(f"END: detect_position_type - Output: position_type={position_type}, signal_type={signal_type}")
        return position_type, signal_type

    def calculate_new_tp(self, current_price: float, stop_loss_price: float, position_type: str,
                         risk_reward: float = 6.0) -> float:
        logger.debug(
            f"START: calculate_new_tp - Input: current_price={current_price}, stop_loss_price={stop_loss_price}, "
            f"position_type={position_type}, risk_reward={risk_reward}")
        distance = abs(current_price - stop_loss_price)
        if position_type == "LONG":
            new_tp = current_price + risk_reward * distance
        else:
            new_tp = current_price - risk_reward * distance
        logger.debug(f"END: calculate_new_tp - Output: new_tp={new_tp}")
        return new_tp

    def adjust_sl_for_exit(self, current_price: float, entry_price: float, current_sl: float,
                           position_type: str) -> float:
        logger.debug(f"START: adjust_sl_for_exit - Input: current_price={current_price}, entry_price={entry_price}, "
                     f"current_sl={current_sl}, position_type={position_type}")
        mid_point = (current_price + entry_price) / 2
        if position_type == "LONG":
            new_sl = max(current_sl, mid_point)
        else:
            new_sl = min(current_sl, mid_point)
        logger.debug(f"END: adjust_sl_for_exit - Output: new_sl={new_sl}")
        return new_sl

    def process_signal(self, data: SignalData) -> dict:
        logger.debug(f"START: process_signal - Input: data={vars(data)}")
        position_type, signal_type = self.detect_position_type(data.alert, data.tp1, data.tp2)
        positions = fetch_all_positions(self.client, data.symbol)
        existing_position = next((pos for pos in positions if pos.position_type == position_type), None)
        opposite_position = next((pos for pos in positions if pos.position_type != position_type), None)
        signal_uuid = str(uuid.uuid4())
        message = "Signal processed"
        actions = {
            "close_opposite": False,
            "create_position": None,
            "take_partial_profit": None,
            "update_sl_tp": None,
            "clear_exit_orders": None
        }

        # Step 1: Calculate all parameters and handle signal logic
        if signal_type == "confirmation":

            if existing_position:
                message = f"{position_type} position exists, no action taken"
            else:
                if opposite_position:
                    actions["close_opposite"] = True
                    message = f"Will close opposite {opposite_position.position_type} position and open {position_type} limit order"

                # Calculate parameters for limit order
                signal_score, _ = score_signal(self.client, data, position_type)
                investment_adj = (signal_score / 100) * INVESTMENT_PERCENTAGE
                new_tp = self.calculate_new_tp(data.close_price, data.sl2, position_type)

                calc_result = calculate_params_with_sl_tp_without_invest_percentage(
                    self.client, data.symbol, position_type, data.sl2, new_tp, investment_adj, LEVERAGE
                )
                if "status" in calc_result and calc_result["status"] == "error":
                    logger.error(f"Calculation failed: {calc_result['message']}")
                    return {"statusCode": 500, "body": json.dumps({"error": calc_result["message"]})}

                # Set limit order price (e.g., current close price)
                limit_price = data.close_price  # Adjustable if needed

                actions["create_position"] = calc_result
                actions["create_position"]["limit_price"] = limit_price  # Add limit price for the order
                actions["clear_exit_orders"] = True
                actions["update_sl_tp"] = {
                    "stop_loss_price": calc_result["stop_loss_price"],
                    "take_profit_price": calc_result["take_profit_price"],
                    "quantity": calc_result["quantity"]
                }
                message = f"Will open new {position_type} limit order at {limit_price}"

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
                    return {"statusCode": 500, "body": json.dumps({"error": calc_result["message"]})}
                actions["create_position"] = calc_result
                actions["clear_exit_orders"] = True
                actions["update_sl_tp"] = {
                    "stop_loss_price": calc_result["stop_loss_price"],
                    "take_profit_price": calc_result["take_profit_price"],
                    "quantity": calc_result["quantity"]
                }
                message = f"Will open new {position_type} position" + \
                          (f" and close {opposite_position.position_type}" if opposite_position else "")
                if opposite_position:
                    actions["close_opposite"] = True

        elif signal_type == "tp2_reach" and existing_position:
            new_tp = self.calculate_new_tp(data.close_price, data.sl1, position_type)
            partial_qty = abs(float(existing_position.positionAmt)) * 0.4
            remaining_qty = abs(float(existing_position.positionAmt)) - partial_qty
            new_sl = (float(data.tp1) + float(existing_position.entryPrice)) / 2
            actions["take_partial_profit"] = {
                "take_profit_price": new_tp,
                "quantity": partial_qty
            }
            actions["clear_exit_orders"] = True
            actions["update_sl_tp"] = {
                "stop_loss_price": new_sl,
                "take_profit_price": new_tp,
                "quantity": remaining_qty
            }
            message = f"Will take 40% profit and adjust SL/TP for {position_type}"

        elif signal_type == "exit" and existing_position:
            current_sl = float(data.sl1)
            new_sl = self.adjust_sl_for_exit(data.close_price, float(existing_position.entryPrice), current_sl,
                                             position_type)
            new_tp = self.calculate_new_tp(data.close_price, new_sl, position_type)
            partial_qty = abs(float(existing_position.positionAmt)) * 0.4
            remaining_qty = abs(float(existing_position.positionAmt)) - partial_qty
            actions["take_partial_profit"] = {
                "take_profit_price": new_tp,
                "quantity": partial_qty
            }
            actions["clear_exit_orders"] = True
            actions["update_sl_tp"] = {
                "stop_loss_price": new_sl,
                "take_profit_price": new_tp,
                "quantity": remaining_qty
            }
            message = f"Will take 40% profit and adjust SL/TP for {position_type}"

        elif signal_type == "sl_reach" and existing_position:
            message = f"SL reached for {position_type}, closing position"
            actions["close_opposite"] = True  # Treat as closing the position

        # Step 2: Execute all actions
        result = {"status": "success", "details": {}}

        logger.info(f"Actions: {actions}")

        if actions["close_opposite"]:
            logger.info(
                f"ACTION: Closing opposite position: {opposite_position.position_type if opposite_position else position_type}")
            close_result = close_position(self.client, data.symbol,
                                          opposite_position.position_type if opposite_position else position_type,
                                          LEVERAGE)
            if close_result.get("status") == "error":
                return {"statusCode": 500, "body": json.dumps({"error": close_result["message"]})}
            result["details"]["close_opposite"] = close_result

        if actions["take_partial_profit"]:
            logger.info(f"ACTION: Taking partial profit for {position_type}")
            partial_result = take_profit_partially(
                self.client, data.symbol, LEVERAGE,
                actions["take_partial_profit"]["take_profit_price"],
                actions["take_partial_profit"]["quantity"]
            )
            if partial_result.get("status") == "error":
                return {"statusCode": 500, "body": json.dumps({"error": partial_result["message"]})}
            result["details"]["take_partial_profit"] = partial_result

        if actions["clear_exit_orders"]:
            logger.info(f"ACTION: Clearing exit orders for {position_type}")
            clear_all_symbol_orders(self.client, data.symbol)

        if actions["create_position"]:
            order_type = "LIMIT" if signal_type == "confirmation" else "MARKET"
            logger.info(f"ACTION: Creating {position_type} {order_type.lower()} order")
            create_result = create_position_order(
                self.client, data.symbol, position_type,
                actions["create_position"]["stop_loss_price"],
                actions["create_position"]["take_profit_price"],
                actions["create_position"]["quantity"],
                actions["create_position"]["investment_amount"],
                actions["create_position"].get("limit_price", actions["create_position"]["market_price"]),  # Use limit_price if available
                LEVERAGE,
                order_type=order_type
            )
            if create_result.get("status") == "error":
                return {"statusCode": 500, "body": json.dumps({"error": create_result["message"]})}
            result["details"]["create_position"] = create_result

        if actions["update_sl_tp"]:
            logger.info(f"ACTION: Updating SL/TP for {position_type}")
            update_result = update_sl_tp_orders(
                self.client, data.symbol, position_type,
                actions["update_sl_tp"]["stop_loss_price"],
                actions["update_sl_tp"]["take_profit_price"],
                actions["update_sl_tp"]["quantity"]
            )
            if update_result.get("status") == "error":
                return {"statusCode": 500, "body": json.dumps({"error": update_result["message"]})}
            result["details"]["update_sl_tp"] = update_result

        # Step 3: Summarize and notify with PNL
        pnl_info = "N/A"
        if "close_opposite" in result["details"]:
            pnl = result["details"]["close_opposite"]["pnl"]
            pnl_info = f"{pnl:.4f} USDT ({result['details']['close_opposite']['pnl_percent_investment']:.2f}%)"
        elif "take_partial_profit" in result["details"]:
            pnl = result["details"]["take_partial_profit"]["pnl"]
            pnl_info = f"{pnl:.4f} USDT ({result['details']['take_partial_profit']['pnl_percent_investment']:.2f}%)"

        discord_table = (
            f"**Signal Processed - {data.symbol} ({signal_type}) [UUID: {signal_uuid}]**\n"
            f"```\n"
            f"{'Field':<15} | {'Value':<20} | {'Field':<15} | {'Value':<20}\n"
            f"{'-' * 15}+{'-' * 20}+{'-' * 15}+{'-' * 20}\n"
            f"{'Position':<15} | {position_type or 'N/A':<20} | {'Message':<15} | {message:<20}\n"
            f"{'Close Price':<15} | {data.close_price:<20.5f} | {'TP1':<15} | {data.tp1 or 0:<20.5f}\n"
            f"{'SL1':<15} | {data.sl1 or 0:<20.5f} | {'TP2':<15} | {data.tp2 or 0:<20.5f}\n"
            f"{'SL2':<15} | {data.sl2 or 0:<20.5f} | {'Score':<15} | {locals().get('signal_score', 'N/A'):<20}\n"
            f"{'Actions':<15} | {', '.join([k for k, v in actions.items() if v]) or 'None':<20}\n"
            f"{'PNL':<15} | {pnl_info:<20}\n"
            f"```\n"
        )
        _send_discord_notification(discord_table)

        response = {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "signal": {
                    "alert": data.alert,
                    "symbol": data.symbol,
                    "position_type": position_type,
                    "close_price": data.close_price,
                    "actions": {k: v for k, v in actions.items() if v},
                    "result": result["details"]
                }
            })
        }
        logger.debug(f"END: process_signal - Output: response={response}")
        return response


def lambda_handler(event: dict, context: object) -> dict:
    logger.debug(f"START: lambda_handler - Input: event={event}, context={context}")
    try:
        processor = TradingSignalProcessor()
        data = processor.extract_event_data(event)
        response = processor.process_signal(data)
        logger.debug(f"END: lambda_handler - Output: response={response}")
        return response
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        _send_discord_notification(f"**ERROR**: Internal server error: {str(e)}")
        error_response = {
            "statusCode": 500,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }
        logger.debug(f"END: lambda_handler - Output (error): response={error_response}")
        return error_response
