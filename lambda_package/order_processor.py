import logging
import math

from binance.um_futures import UMFutures
from binance.error import ClientError
from binance_trade_wrapper import place_order, place_market_order, get_exchange_info, get_rounded_price

# Configure logging with file and function prefix
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

LEVERAGE = 10  # 10x leverage

def place_stop_loss_order(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float, quantity: float):
    logging.info("START: place_stop_loss_order")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        stop_loss_order = place_order(client, symbol, side, "STOP_MARKET",
                                      price=stop_loss_price, quantity=quantity,
                                      close_position=True)
        logging.info(f"Stop loss order placed: {stop_loss_order}")
        logging.info("END: place_stop_loss_order - Success")
        return stop_loss_order
    except ClientError as error:
        logging.error(f"ClientError in stop-loss - {error.error_message}")
        return None


def place_take_profit_order(client: UMFutures, symbol: str, position_type: str, take_profit_price: float,
                            quantity: float):
    logging.info("START: place_take_profit_order")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        take_profit_order = place_order(client, symbol, side, "TAKE_PROFIT_MARKET",
                                        price=take_profit_price, quantity=quantity,
                                        close_position=True)
        logging.info(f"Take profit order placed: {take_profit_order}")
        logging.info("END: place_take_profit_order - Success")
        return take_profit_order
    except ClientError as error:
        logging.error(f"ClientError in take-profit - {error.error_message}")
        return None


def create_order_with_sl_tp(client: UMFutures, symbol: str, position_type: str,
                            stop_loss_price: float, take_profit_price: float,
                            quantity: float, investment_amount: float, market_price: float):
    logging.info("START: create_order_with_sl_tp")
    logging.info(f"Parameters - symbol: {symbol}, position_type: {position_type}, "
                 f"stop_loss_price: {stop_loss_price}, take_profit_price: {take_profit_price}, "
                 f"quantity: {quantity}, investment_amount: {investment_amount}, market_price: {market_price}")

    try:
        if not clear_all_symbol_orders(client, symbol):
            logging.error(f"Failed to cancel existing orders for {symbol}")

        logging.info(f"Using calculated values - SL: {stop_loss_price}, TP: {take_profit_price}, Quantity: {quantity}")

        try:
            client.change_leverage(symbol=symbol, leverage=LEVERAGE)
            logging.info(f"Leverage set to {LEVERAGE}x for {symbol}")
        except Exception as e:
            logging.error(f"Failed to change leverage: {e}")
            return {"status": "error", "message": "Failed to change leverage"}

        side = "BUY" if position_type == "LONG" else "SELL"
        order = place_market_order(client, symbol, side, LEVERAGE, quantity=quantity)
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
        logging.info("END: create_order_with_sl_tp - Success")
        return result
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return {"status": "error", "message": str(e)}


def close_position(client: UMFutures, symbol: str, position_type: str):
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

        close_order = place_market_order(client, symbol, side, LEVERAGE, quantity=position_qty)
        if not close_order:
            logging.error("Failed to place close order")
            return {"status": "error", "message": "Failed to close position"}

        logging.info(f"Close order placed: {close_order}")
        logging.info(f"END: close_position - {position_type} for {symbol} - Success")
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
    logging.info(f"START: clear_all_symbol_orders - {symbol}")
    try:
        client.cancel_open_orders(symbol=symbol)
        return True
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return False


def take_profit_partially(client: UMFutures, symbol: str) -> dict:
    """
    Takes partial profit (50%) and adjusts stop loss to midpoint between entry and current price.

    Args:
        client: UMFutures client instance
        symbol: Trading pair symbol (e.g., "DOGEUSDT")

    Returns:
        dict: Result of the operation with status and details
    """
    logging.info(f"START: take_profit_partially - symbol: {symbol}")
    try:
        # Get current position information
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0), None)

        if not position:
            logging.info(f"No open position found for {symbol}")
            return {"status": "error", "message": "No open position found"}

        # Get current market price
        ticker = client.ticker_price(symbol=symbol)
        current_price = float(ticker["price"])

        # Original position details
        entry_price = float(position["entryPrice"])
        position_qty = abs(float(position["positionAmt"]))
        position_type = "LONG" if float(position["positionAmt"]) > 0 else "SHORT"

        # Calculate partial quantity (50% of position)
        partial_qty = round(position_qty * 0.5, get_exchange_info(client, symbol)[1])

        # Calculate new stop loss (midpoint between entry and current price)
        new_stop_loss = (entry_price + current_price) / 2
        new_stop_loss = get_rounded_price(client, symbol, new_stop_loss)

        # Get existing TP price from open orders
        take_profit_price = current_price + abs(entry_price - current_price) * 2 if position_type == "LONG" else current_price - abs(entry_price - current_price) * 2

        # 1. Take partial profit at current market price
        side = "SELL" if position_type == "LONG" else "BUY"
        partial_order = place_market_order(client, symbol, side, LEVERAGE, quantity=partial_qty)

        if not partial_order:
            logging.error("Failed to place partial profit order")
            return {"status": "error", "message": "Partial profit order failed"}

        # 2. Cancel existing stop loss
        if not clear_all_symbol_orders(client, symbol):
            logging.error(f"Failed to cancel existing orders for {symbol}")

        # 3. Place new stop loss at midpoint
        remaining_qty = position_qty - partial_qty
        stop_loss_order = place_stop_loss_order(client, symbol, position_type, new_stop_loss, remaining_qty)

        # 4. Place take profit for remaining quantity
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
        logging.info("END: take_profit_partially - Success")
        return result

    except Exception as e:
        logging.error(f"Error in take_profit_partially: {str(e)}")
        return {"status": "error", "message": str(e)}