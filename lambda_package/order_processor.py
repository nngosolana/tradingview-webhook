import logging
from typing import Dict

from binance.error import ClientError
from binance.um_futures import UMFutures
from binance_trade_wrapper import place_order, place_market_order, get_rounded_price, get_exchange_info
from config import TRANSACTION_FEE_RATE

logging.basicConfig(
    level=logging.DEBUG,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def place_stop_loss_order(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float,
                          quantity: float) -> Dict:
    logger.debug(f"START: place_stop_loss_order - Input: client={client}, symbol={symbol}, position_type={position_type}, "
                 f"stop_loss_price={stop_loss_price}, quantity={quantity}")
    try:
        # Round values
        rounded_stop_loss = get_rounded_price(client, symbol, stop_loss_price)
        _, quantity_precision = get_exchange_info(client, symbol)
        rounded_quantity = round(quantity, quantity_precision) if quantity_precision is not None else quantity

        logger.debug(f"Rounded values: stop_loss_price={rounded_stop_loss}, quantity={rounded_quantity}")

        side = "SELL" if position_type == "LONG" else "BUY"
        stop_loss_order = place_order(client, symbol, side, "STOP_MARKET",
                                      price=rounded_stop_loss, quantity=rounded_quantity, reduce_only=True)
        if not stop_loss_order:
            result = {"status": "error", "message": "Failed to place stop loss order"}
        else:
            result = {"status": "success", "stop_loss_order": stop_loss_order}
        logger.debug(f"END: place_stop_loss_order - Output: result={result}")
        return result
    except ClientError as error:
        logger.error(f"ClientError in stop-loss: {error.error_message}")
        result = {"status": "error", "message": error.error_message}
        logger.debug(f"END: place_stop_loss_order - Output (error): result={result}")
        return result

def create_position_order(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float,
                          take_profit_price: float, quantity: float, investment_amount: float,
                          market_price: float, leverage: int) -> Dict:
    logger.debug(f"START: create_position_order - Input: client={client}, symbol={symbol}, position_type={position_type}, "
                 f"stop_loss_price={stop_loss_price}, take_profit_price={take_profit_price}, quantity={quantity}, "
                 f"investment_amount={investment_amount}, market_price={market_price}, leverage={leverage}")
    try:
        # Round values
        rounded_stop_loss = get_rounded_price(client, symbol, stop_loss_price)
        rounded_take_profit = get_rounded_price(client, symbol, take_profit_price)
        rounded_market_price = get_rounded_price(client, symbol, market_price)
        _, quantity_precision = get_exchange_info(client, symbol)
        rounded_quantity = round(quantity, quantity_precision) if quantity_precision is not None else quantity

        logger.debug(f"Rounded values: stop_loss_price={rounded_stop_loss}, take_profit_price={rounded_take_profit}, "
                     f"quantity={rounded_quantity}, market_price={rounded_market_price}")

        client.change_leverage(symbol=symbol, leverage=leverage)
        side = "BUY" if position_type == "LONG" else "SELL"
        order = place_market_order(client, symbol, side, leverage, quantity=rounded_quantity)
        if not order:
            result = {"status": "error", "message": "Order placement failed"}
        else:
            result = {
                "status": "success",
                "order": order,
                "trade_amount": investment_amount,
                "quantity": rounded_quantity,
                "market_price": rounded_market_price
            }
        logger.debug(f"END: create_position_order - Output: result={result}")
        return result
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        result = {"status": "error", "message": str(e)}
        logger.debug(f"END: create_position_order - Output (error): result={result}")
        return result

def _calculate_pnl(client: UMFutures, symbol: str, position_type: str, exit_price: float, quantity: float) -> Dict:
    logger.debug(f"START: _calculate_pnl - Input: client={client}, symbol={symbol}, position_type={position_type}, "
                 f"exit_price={exit_price}, quantity={quantity}")
    try:
        # Round values
        rounded_exit_price = get_rounded_price(client, symbol, exit_price)
        _, quantity_precision = get_exchange_info(client, symbol)
        rounded_quantity = round(quantity, quantity_precision) if quantity_precision is not None else quantity

        logger.debug(f"Rounded values: exit_price={rounded_exit_price}, quantity={rounded_quantity}")

        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)
        if not position:
            logger.warning(f"No active position found for {symbol}")
            result = {"pnl": 0.0, "investment": 0.0, "pnl_percent_investment": 0.0, "pnl_percent_balance": 0.0}
        else:
            entry_price = float(position["entryPrice"])
            direction = 1 if position_type == "LONG" else -1
            raw_pnl = (rounded_exit_price - entry_price) * rounded_quantity * direction
            total_fees = (entry_price + rounded_exit_price) * rounded_quantity * TRANSACTION_FEE_RATE
            net_pnl = raw_pnl - total_fees
            investment = float(position["positionInitialMargin"])
            total_balance = float(client.account()["totalWalletBalance"])
            pnl_percent_investment = (net_pnl / investment) * 100 if investment > 0 else 0.0
            pnl_percent_balance = (net_pnl / total_balance) * 100 if total_balance > 0 else 0.0
            result = {
                "pnl": round(net_pnl, 4),  # Round to 4 decimals for clarity
                "investment": investment,
                "pnl_percent_investment": round(pnl_percent_investment, 2),
                "pnl_percent_balance": round(pnl_percent_balance, 2)
            }
        logger.debug(f"END: _calculate_pnl - Output: result={result}")
        return result
    except Exception as e:
        logger.error(f"Error calculating PNL: {str(e)}")
        result = {"pnl": 0.0, "investment": 0.0, "pnl_percent_investment": 0.0, "pnl_percent_balance": 0.0}
        logger.debug(f"END: _calculate_pnl - Output (error): result={result}")
        return result

def close_position(client: UMFutures, symbol: str, position_type: str, leverage: int) -> Dict:
    logger.debug(f"START: close_position - Input: client={client}, symbol={symbol}, position_type={position_type}, "
                 f"leverage={leverage}")
    try:
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)
        if not position or float(position["positionAmt"]) == 0:
            logger.info(f"No open {position_type} position found for {symbol}")
            result = {"status": "success", "message": f"No open {position_type} position to close"}
        else:
            position_qty = abs(float(position["positionAmt"]))
            rounded_qty = round(position_qty, get_exchange_info(client, symbol)[1]) if get_exchange_info(client, symbol)[1] is not None else position_qty
            current_price = get_rounded_price(client, symbol, float(client.ticker_price(symbol=symbol)["price"]))
            side = "SELL" if position_type == "LONG" else "BUY"

            logger.debug(f"Rounded values: quantity={rounded_qty}, current_price={current_price}")

            close_order = place_market_order(client, symbol, side, leverage, quantity=rounded_qty, reduce_only=True)
            if not close_order:
                result = {"status": "error", "message": "Failed to close position"}
            else:
                pnl_data = _calculate_pnl(client, symbol, position_type, current_price, rounded_qty)
                result = {
                    "status": "success",
                    "close_order": close_order,
                    "closed_quantity": rounded_qty,
                    "pnl": pnl_data["pnl"],
                    "investment": pnl_data["investment"],
                    "pnl_percent_investment": pnl_data["pnl_percent_investment"],
                    "pnl_percent_balance": pnl_data["pnl_percent_balance"]
                }
        logger.debug(f"END: close_position - Output: result={result}")
        return result
    except Exception as e:
        logger.error(f"Error while closing position: {str(e)}")
        result = {"status": "error", "message": str(e)}
        logger.debug(f"END: close_position - Output (error): result={result}")
        return result

def clear_all_symbol_orders(client: UMFutures, symbol: str) -> bool:
    logger.debug(f"START: clear_all_symbol_orders - Input: client={client}, symbol={symbol}")
    try:
        client.cancel_open_orders(symbol=symbol)
        logger.info("Open orders cancelled successfully")
        result = True
    except Exception as e:
        logger.error(f"Failed to cancel orders: {str(e)}")
        result = False
    logger.debug(f"END: clear_all_symbol_orders - Output: result={result}")
    return result

def take_profit_partially(client: UMFutures, symbol: str, leverage: int, take_profit_price: float,
                          quantity: float) -> Dict:
    logger.debug(f"START: take_profit_partially - Input: client={client}, symbol={symbol}, leverage={leverage}, "
                 f"take_profit_price={take_profit_price}, quantity={quantity}")
    try:
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)
        if not position:
            result = {"status": "error", "message": "No open position found"}
        else:
            # Round values
            rounded_take_profit = get_rounded_price(client, symbol, take_profit_price)
            _, quantity_precision = get_exchange_info(client, symbol)
            rounded_quantity = round(quantity, quantity_precision) if quantity_precision is not None else quantity
            total_qty = abs(float(position["positionAmt"]))
            rounded_total_qty = round(total_qty, quantity_precision) if quantity_precision is not None else total_qty
            current_price = get_rounded_price(client, symbol, float(client.ticker_price(symbol=symbol)["price"]))

            logger.debug(f"Rounded values: take_profit_price={rounded_take_profit}, quantity={rounded_quantity}, "
                         f"total_quantity={rounded_total_qty}, current_price={current_price}")

            if rounded_quantity > rounded_total_qty:
                result = {"status": "error", "message": "Partial quantity exceeds position size"}
            else:
                remaining_qty = rounded_total_qty - rounded_quantity
                position_type = "LONG" if float(position["positionAmt"]) > 0 else "SHORT"
                side = "SELL" if position_type == "LONG" else "BUY"
                partial_order = place_market_order(client, symbol, side, leverage, quantity=rounded_quantity,
                                                   reduce_only=True)
                if not partial_order:
                    result = {"status": "error", "message": "Partial profit order failed"}
                else:
                    pnl_data = _calculate_pnl(client, symbol, position_type, current_price, rounded_quantity)
                    result = {
                        "status": "success",
                        "partial_order": partial_order,
                        "partial_quantity": rounded_quantity,
                        "remaining_quantity": remaining_qty,
                        "market_price": current_price,
                        "pnl": pnl_data["pnl"],
                        "investment": pnl_data["investment"],
                        "pnl_percent_investment": pnl_data["pnl_percent_investment"],
                        "pnl_percent_balance": pnl_data["pnl_percent_balance"]
                    }
        logger.debug(f"END: take_profit_partially - Output: result={result}")
        return result
    except Exception as e:
        logger.error(f"Error in take_profit_partially: {str(e)}")
        result = {"status": "error", "message": str(e)}
        logger.debug(f"END: take_profit_partially - Output (error): result={result}")
        return result

def update_sl_tp_orders(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float,
                        take_profit_price: float, quantity: float) -> Dict:
    """
    Update the Stop Loss and Take Profit directly tied to the position in Binance Futures.
    Rounds prices and quantity to symbol-specific precision.
    """
    logger.debug(f"START: update_sl_tp_orders - Input: client={client}, symbol={symbol}, position_type={position_type}, "
                 f"stop_loss_price={stop_loss_price}, take_profit_price={take_profit_price}, quantity={quantity}")
    try:
        # Round values
        rounded_stop_loss = get_rounded_price(client, symbol, stop_loss_price)
        rounded_take_profit = get_rounded_price(client, symbol, take_profit_price)
        _, quantity_precision = get_exchange_info(client, symbol)
        rounded_quantity = round(quantity, quantity_precision) if quantity_precision is not None else quantity

        logger.debug(f"Rounded values: stop_loss_price={rounded_stop_loss}, take_profit_price={rounded_take_profit}, "
                     f"quantity={rounded_quantity}")

        # Determine the side based on position type
        side = "SELL" if position_type == "LONG" else "BUY"

        # Prepare order parameters
        stop_loss_params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": str(rounded_stop_loss),
            "quantity": str(rounded_quantity),
            "closePosition": "True"
        }
        take_profit_params = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": str(rounded_take_profit),
            "quantity": str(rounded_quantity),
            "closePosition": "True"
        }

        # Place Stop Loss order
        stop_loss_order = client.new_order(**stop_loss_params)
        if not stop_loss_order:
            result = {"status": "error", "message": "Failed to place stop loss order"}
            logger.debug(f"END: update_sl_tp_orders - Output (error): result={result}")
            return result

        # Place Take Profit order
        take_profit_order = client.new_order(**take_profit_params)
        if not take_profit_order:
            result = {"status": "error", "message": "Failed to place take profit order"}
            logger.debug(f"END: update_sl_tp_orders - Output (error): result={result}")
            return result

        # Success result
        result = {
            "status": "success",
            "stop_loss_order": stop_loss_order,
            "take_profit_order": take_profit_order,
            "quantity": rounded_quantity
        }
        logger.debug(f"END: update_sl_tp_orders - Output: result={result}")
        return result

    except ClientError as e:
        logger.error(f"Binance ClientError in update_sl_tp_orders: {e.error_message}")
        result = {"status": "error", "message": f"Binance API error: {e.error_message}"}
        logger.debug(f"END: update_sl_tp_orders - Output (error): result={result}")
        return result
    except Exception as e:
        logger.error(f"Unexpected error in update_sl_tp_orders: {str(e)}")
        result = {"status": "error", "message": str(e)}
        logger.debug(f"END: update_sl_tp_orders - Output (error): result={result}")
        return result