import json
import logging
from typing import Dict

from binance.error import ClientError
from binance.um_futures import UMFutures
from binance_trade_wrapper import get_binance_client, place_order, place_market_order, get_exchange_info, \
    get_rounded_price
from config import INVESTMENT_PERCENTAGE, MAX_LOSS_PERCENTAGE, LEVERAGE, RISK_REWARD_RATIO
from price_calculation_processor import get_current_balance_in_usdt, calculate_sl_tp_prices, \
    calculate_params_with_sl_tp_wihtout_invest_percentage

# Configure logging only once at module level

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)


def handle_order_logic(event: Dict) -> Dict:
    """Handle order logic directly instead of invoking a separate Lambda."""
    logging.info("START: handle_order_logic")
    logging.info(f"Event received: {json.dumps(event)}")
    try:
        body = json.loads(event["body"]) if "body" in event else event
        client = get_binance_client()
        symbol = body.get("symbol")
        action = body.get("action")
        leverage = body.get("leverage", LEVERAGE)  # Use default from config if not provided

        if not symbol or not action:
            raise ValueError("Missing symbol or action")

        if action == "get_balance":
            result = get_current_balance_in_usdt(client)
        elif action in ["open_long_sl_tp", "open_short_sl_tp"]:
            position_type = "LONG" if action == "open_long_sl_tp" else "SHORT"

            # Close existing positions
            if position_type == "LONG":
                close_result = close_position(client, symbol, "SHORT", leverage)
                logging.info(f"Close SHORT: {close_result}")
            elif position_type == "SHORT":
                close_result = close_position(client, symbol, "LONG", leverage)
                logging.info(f"Close LONG: {close_result}")

            calc_result = calculate_sl_tp_prices(client, symbol, position_type,
                                                 INVESTMENT_PERCENTAGE, MAX_LOSS_PERCENTAGE,
                                                 RISK_REWARD_RATIO, leverage)
            if "status" in calc_result and calc_result["status"] == "error":
                logging.error(f"Calculation failed: {calc_result['message']}")
                return calc_result
            result = create_order_with_sl_tp(client, symbol, position_type, **calc_result, leverage=leverage)
        elif action in ["open_long_sl_tp_without_investment", "open_short_sl_tp_without_investment"]:
            position_type = "LONG" if action == "open_long_sl_tp_without_investment" else "SHORT"

            # Close existing positions
            if position_type == "LONG":
                close_result = close_position(client, symbol, "SHORT", leverage)
                logging.info(f"Close SHORT: {close_result}")
            elif position_type == "SHORT":
                close_result = close_position(client, symbol, "LONG", leverage)
                logging.info(f"Close LONG: {close_result}")

            calc_result = calculate_params_with_sl_tp_wihtout_invest_percentage(
                client, symbol, position_type,
                float(body["stop_loss_price"]),
                float(body["take_profit_price"]),
                INVESTMENT_PERCENTAGE, leverage
            )
            if "status" in calc_result and calc_result["status"] == "error":
                logging.error(f"Calculation failed: {calc_result['message']}")
                return calc_result
            result = create_order_with_sl_tp(client, symbol, position_type, **calc_result, leverage=leverage)
        elif action == "close_all_symbol_orders":
            result = {}
            for pos_type in ["LONG", "SHORT"]:
                result[pos_type] = close_position(client, symbol, pos_type, leverage)
                logging.info(f"Close {pos_type}: {result[pos_type]}")
        elif action == "take_profit_partially":
            result = take_profit_partially(client, symbol, leverage)
        elif action == "clear_orders":
            result = clear_all_symbol_orders(client, symbol)
        else:
            result = {"status": "error", "message": "Invalid action"}

        logging.info(f"Order logic result: {result}")
        return result
    except Exception as e:
        logging.error(f"Unhandled Exception: {str(e)}")
        return {"status": "error", "message": str(e)}


def place_stop_loss_order(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float, quantity: float):
    logging.info(f"START: place_stop_loss_order - symbol: {symbol}, position_type: {position_type}")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        stop_loss_order = place_order(client, symbol, side, "STOP_MARKET",
                                      price=stop_loss_price, quantity=quantity,
                                      close_position=True)
        logging.info(f"Stop loss order placed: {stop_loss_order}")
        return stop_loss_order
    except ClientError as error:
        logging.error(f"ClientError in stop-loss: {error.error_message}")
        return None


def place_take_profit_order(client: UMFutures, symbol: str, position_type: str, take_profit_price: float,
                            quantity: float):
    logging.info(f"START: place_take_profit_order - symbol: {symbol}, position_type: {position_type}")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        take_profit_order = place_order(client, symbol, side, "TAKE_PROFIT_MARKET",
                                        price=take_profit_price, quantity=quantity,
                                        close_position=True)
        logging.info(f"Take profit order placed: {take_profit_order}")
        return take_profit_order
    except ClientError as error:
        logging.error(f"ClientError in take-profit: {error.error_message}")
        return None


def create_order_with_sl_tp(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float,
                            take_profit_price: float, quantity: float, investment_amount: float,
                            market_price: float, leverage: int):
    logging.info(f"START: create_order_with_sl_tp - symbol: {symbol}, position_type: {position_type}")
    try:
        if not clear_all_symbol_orders(client, symbol):
            logging.error(f"Failed to cancel existing orders for {symbol}")

        client.change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage set to {leverage}x for {symbol}")

        side = "BUY" if position_type == "LONG" else "SELL"
        order = place_market_order(client, symbol, side, leverage, quantity=quantity)
        if not order:
            logging.error("Order placement failed")
            return {"status": "error", "message": "Order placement failed"}

        logging.info(f"Market order placed: {order}")

        stop_loss_order = place_stop_loss_order(client, symbol, position_type, stop_loss_price, quantity)
        take_profit_order = place_take_profit_order(client, symbol, position_type, take_profit_price, quantity)

        result = {
            "status": "success",
            "order": order,
            "stop_loss_order": stop_loss_order,
            "take_profit_order": take_profit_order,
            "trade_amount": investment_amount,
            "quantity": quantity,
            "calculated_sl": stop_loss_price,
            "calculated_tp": take_profit_price,
            "market_price": market_price
        }
        logging.info(f"Position opened successfully: {result}")
        return result
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return {"status": "error", "message": str(e)}


def close_position(client: UMFutures, symbol: str, position_type: str, leverage: int):
    logging.info(f"START: close_position - {position_type} for {symbol}")
    try:
        if not clear_all_symbol_orders(client, symbol):
            logging.error(f"Failed to cancel existing orders for {symbol}")

        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol), None)

        if not position or float(position["positionAmt"]) == 0:
            logging.info(f"No open {position_type} position found for {symbol}")
            return {"status": "success", "message": f"No open {position_type} position to close"}

        position_qty = abs(float(position["positionAmt"]))
        side = "SELL" if position_type == "LONG" else "BUY"
        close_order = place_market_order(client, symbol, side, leverage, quantity=position_qty)

        if not close_order:
            logging.error("Failed to place close order")
            return {"status": "error", "message": "Failed to close position"}

        logging.info(f"Close order placed: {close_order}")
        return {
            "status": "success",
            "close_order": close_order,
            "closed_quantity": position_qty
        }
    except ClientError as e:
        logging.error(f"ClientError while closing position: {e.error_message}")
        return {"status": "error", "message": str(e.error_message)}
    except Exception as e:
        logging.error(f"Unexpected error while closing position: {str(e)}")
        return {"status": "error", "message": str(e)}


def clear_all_symbol_orders(client: UMFutures, symbol: str):
    logging.info(f"START: clear_all_symbol_orders - symbol: {symbol}")
    try:
        client.cancel_open_orders(symbol=symbol)
        logging.info("Open orders cancelled successfully")
        return True
    except Exception as e:
        logging.error(f"Failed to cancel orders: {str(e)}")
        return False


def take_profit_partially(client: UMFutures, symbol: str, leverage: int) -> Dict:
    logging.info(f"START: take_profit_partially - symbol: {symbol}")
    try:
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)

        if not position:
            logging.info(f"No open position found for {symbol}")
            return {"status": "error", "message": "No open position found"}

        current_price = float(client.ticker_price(symbol=symbol)["price"])
        entry_price = float(position["entryPrice"])
        position_qty = abs(float(position["positionAmt"]))
        position_type = "LONG" if float(position["positionAmt"]) > 0 else "SHORT"

        partial_qty = round(position_qty * 0.5, get_exchange_info(client, symbol)[1])
        new_stop_loss = get_rounded_price(client, symbol, (entry_price + current_price) / 2)
        take_profit_price = (current_price + abs(entry_price - current_price) * 2
                             if position_type == "LONG"
                             else current_price - abs(entry_price - current_price) * 2)

        side = "SELL" if position_type == "LONG" else "BUY"
        partial_order = place_market_order(client, symbol, side, leverage, quantity=partial_qty)
        if not partial_order:
            logging.error("Failed to place partial profit order")
            return {"status": "error", "message": "Partial profit order failed"}

        if not clear_all_symbol_orders(client, symbol):
            logging.error(f"Failed to cancel existing orders for {symbol}")

        remaining_qty = position_qty - partial_qty
        stop_loss_order = place_stop_loss_order(client, symbol, position_type, new_stop_loss, remaining_qty)
        take_profit_order = place_take_profit_order(client, symbol, position_type, take_profit_price, remaining_qty)

        result = {
            "status": "success",
            "partial_order": partial_order,
            "new_stop_loss_order": stop_loss_order,
            "new_take_profit_order": take_profit_order,
            "partial_quantity": partial_qty,
            "remaining_quantity": remaining_qty,
            "new_stop_loss_price": new_stop_loss,
            "take_profit_price": take_profit_price,
            "market_price": current_price
        }
        logging.info(f"Partial profit taken successfully: {result}")
        return result
    except Exception as e:
        logging.error(f"Error in take_profit_partially: {str(e)}")
        return {"status": "error", "message": str(e)}
