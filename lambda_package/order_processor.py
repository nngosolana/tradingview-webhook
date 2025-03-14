import logging
from typing import Optional, Dict

from binance.error import ClientError
from binance.um_futures import UMFutures
from binance_trade_wrapper import get_binance_client, place_order, place_market_order, get_exchange_info, \
    get_rounded_price
from config import (INVESTMENT_PERCENTAGE, MAX_LOSS_PERCENTAGE, LEVERAGE, RISK_REWARD_RATIO,
                    TRANSACTION_FEE_RATE)
from utils import _send_discord_notification
from price_calculation_processor import (get_current_balance_in_usdt, calculate_sl_tp_prices,
                                         calculate_params_with_sl_tp_without_invest_percentage)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def handle_order_logic(action: str, symbol: str, position_type: Optional[str] = None,
                       stop_loss_price: Optional[float] = None, take_profit_price: Optional[float] = None,
                       quantity: Optional[float] = None, investment_percentage: Optional[float] = None,
                       leverage: Optional[int] = LEVERAGE) -> Dict:
    """Handle order logic with direct parameters."""
    logger.info(f"START: handle_order_logic - Action: {action}, Symbol: {symbol}")
    try:
        client = get_binance_client()
        if not symbol or not action:
            raise ValueError("Missing symbol or action")

        if action == "get_balance":
            result = get_current_balance_in_usdt(client)
        elif action in ["open_long_sl_tp", "open_short_sl_tp"]:
            position_type = "LONG" if action == "open_long_sl_tp" else "SHORT"
            if position_type == "LONG":
                close_result = close_position(client, symbol, "SHORT", leverage)
                logger.info(f"Close SHORT: {close_result}")
            elif position_type == "SHORT":
                close_result = close_position(client, symbol, "LONG", leverage)
                logger.info(f"Close LONG: {close_result}")
            calc_result = calculate_sl_tp_prices(client, symbol, position_type,
                                                 INVESTMENT_PERCENTAGE, MAX_LOSS_PERCENTAGE,
                                                 RISK_REWARD_RATIO, leverage)
            if "status" in calc_result and calc_result["status"] == "error":
                logger.error(f"Calculation failed: {calc_result['message']}")
                return calc_result
            result = create_order_with_sl_tp(client, symbol, position_type, **calc_result, leverage=leverage)
        elif action in ["open_long_sl_tp_without_investment", "open_short_sl_tp_without_investment"]:
            position_type = "LONG" if action == "open_long_sl_tp_without_investment" else "SHORT"
            if position_type == "LONG":
                close_result = close_position(client, symbol, "SHORT", leverage)
                logger.info(f"Close SHORT: {close_result}")
            elif position_type == "SHORT":
                close_result = close_position(client, symbol, "LONG", leverage)
                logger.info(f"Close LONG: {close_result}")
            calc_result = calculate_params_with_sl_tp_without_invest_percentage(
                client, symbol, position_type, stop_loss_price, take_profit_price,
                investment_percentage or INVESTMENT_PERCENTAGE, leverage
            )
            if "status" in calc_result and calc_result["status"] == "error":
                logger.error(f"Calculation failed: {calc_result['message']}")
                return calc_result
            result = create_order_with_sl_tp(client, symbol, position_type, **calc_result, leverage=leverage)
        elif action == "close_all_symbol_orders":
            result = {}
            for pos_type in ["LONG", "SHORT"]:
                result[pos_type] = close_position(client, symbol, pos_type, leverage)
                logger.info(f"Close {pos_type}: {result[pos_type]}")
        elif action == "take_profit_partially":
            result = take_profit_partially(client, symbol, leverage)
        elif action == "clear_orders":
            result = clear_all_symbol_orders(client, symbol)
        elif action == "place_stop_loss":
            result = place_stop_loss_order(client, symbol, position_type, stop_loss_price, quantity)
            if result is None:
                result = {"status": "error", "message": "Failed to place stop loss"}
            else:
                result = {"status": "success", "stop_loss_order": result}
        elif action == "place_take_profit":
            result = place_take_profit_order(client, symbol, position_type, take_profit_price, quantity)
            if result is None:
                result = {"status": "error", "message": "Failed to place take profit"}
            else:
                result = {"status": "success", "take_profit_order": result}
        else:
            result = {"status": "error", "message": "Invalid action"}

        logger.info(f"Order logic result: {result}")
        return result
    except Exception as e:
        logger.error(f"Unhandled Exception: {str(e)}")
        return {"status": "error", "message": str(e)}


def place_stop_loss_order(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float, quantity: float):
    logger.info(f"START: place_stop_loss_order - symbol: {symbol}, position_type: {position_type}")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        stop_loss_order = place_order(client, symbol, side, "STOP_MARKET",
                                      price=stop_loss_price, quantity=quantity,
                                      close_position=True)
        logger.info(f"Stop loss order placed: {stop_loss_order}")
        return stop_loss_order
    except ClientError as error:
        logger.error(f"ClientError in stop-loss: {error.error_message}")
        return None


def place_take_profit_order(client: UMFutures, symbol: str, position_type: str, take_profit_price: float,
                            quantity: float):
    logger.info(f"START: place_take_profit_order - symbol: {symbol}, position_type: {position_type}")
    try:
        side = "SELL" if position_type == "LONG" else "BUY"
        take_profit_order = place_order(client, symbol, side, "TAKE_PROFIT_MARKET",
                                        price=take_profit_price, quantity=quantity,
                                        close_position=True)
        logger.info(f"Take profit order placed: {take_profit_order}")
        return take_profit_order
    except ClientError as error:
        logger.error(f"ClientError in take-profit: {error.error_message}")
        return None


def create_order_with_sl_tp(client: UMFutures, symbol: str, position_type: str, stop_loss_price: float,
                            take_profit_price: float, quantity: float, investment_amount: float,
                            market_price: float, leverage: int):
    logger.info(f"START: create_order_with_sl_tp - symbol: {symbol}, position_type: {position_type}")
    try:
        if not clear_all_symbol_orders(client, symbol):
            logger.error(f"Failed to cancel existing orders for {symbol}")

        client.change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"Leverage set to {leverage}x for {symbol}")

        side = "BUY" if position_type == "LONG" else "SELL"
        order = place_market_order(client, symbol, side, leverage, quantity=quantity)
        if not order:
            logger.error("Order placement failed")
            return {"status": "error", "message": "Order placement failed"}

        logger.info(f"Market order placed: {order}")

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
        logger.info(f"Position opened successfully: {result}")
        return result
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {"status": "error", "message": str(e)}


def _calculate_pnl(client: UMFutures, symbol: str, position_type: str, exit_price: float, quantity: float) -> Dict:
    """Calculate PNL including fees and percentages."""
    try:
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)
        if not position:
            logger.warning(f"No active position found for {symbol}")
            return {"pnl": 0.0, "investment": 0.0, "pnl_percent_investment": 0.0, "pnl_percent_balance": 0.0}

        entry_price = float(position["entryPrice"])
        direction = 1 if position_type == "LONG" else -1

        # Calculate raw PNL
        raw_pnl = (exit_price - entry_price) * quantity * direction

        # Calculate fees (entry + exit)
        entry_fee = entry_price * quantity * TRANSACTION_FEE_RATE
        exit_fee = exit_price * quantity * TRANSACTION_FEE_RATE
        total_fees = entry_fee + exit_fee

        # Net PNL
        net_pnl = raw_pnl - total_fees

        # Get investment and total balance
        investment = float(position["positionInitialMargin"])
        account_info = client.account()
        total_balance = float(account_info["totalWalletBalance"])

        # Calculate percentages
        pnl_percent_investment = (net_pnl / investment) * 100 if investment > 0 else 0.0
        pnl_percent_balance = (net_pnl / total_balance) * 100 if total_balance > 0 else 0.0

        logger.info(f"PNL Calculation - Symbol: {symbol}, Entry: {entry_price}, Exit: {exit_price}, "
                    f"Quantity: {quantity}, Raw PNL: {raw_pnl}, Fees: {total_fees}, Net PNL: {net_pnl}, "
                    f"Investment: {investment}, Total Balance: {total_balance}, "
                    f"PNL % Investment: {pnl_percent_investment}, PNL % Balance: {pnl_percent_balance}")

        return {
            "pnl": net_pnl,
            "investment": investment,
            "pnl_percent_investment": pnl_percent_investment,
            "pnl_percent_balance": pnl_percent_balance
        }
    except Exception as e:
        logger.error(f"Error calculating PNL: {str(e)}")
        return {"pnl": 0.0, "investment": 0.0, "pnl_percent_investment": 0.0, "pnl_percent_balance": 0.0}


def close_position(client: UMFutures, symbol: str, position_type: str, leverage: int):
    logger.info(f"START: close_position - {position_type} for {symbol}")
    try:
        if not clear_all_symbol_orders(client, symbol):
            logger.error(f"Failed to cancel existing orders for {symbol}")

        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol), None)

        if not position or float(position["positionAmt"]) == 0:
            logger.info(f"No open {position_type} position found for {symbol}")
            return {"status": "success", "message": f"No open {position_type} position to close"}

        position_qty = abs(float(position["positionAmt"]))
        side = "SELL" if position_type == "LONG" else "BUY"
        current_price = float(client.ticker_price(symbol=symbol)["price"])
        close_order = place_market_order(client, symbol, side, leverage, quantity=position_qty)

        if not close_order:
            logger.error("Failed to place close order")
            return {"status": "error", "message": "Failed to close position"}

        # Calculate PNL
        pnl_data = _calculate_pnl(client, symbol, position_type, current_price, position_qty)

        if pnl_data["pnl"] != 0 and pnl_data['investment'] != 0:
            # Send Discord notification
            discord_msg = (f"-------------------------CLOSE POSITION---------------------------------\n"
                           f"Position Closed - {symbol} ({position_type})\n"
                           f"PNL: {pnl_data['pnl']:.2f} USDT\n"
                           f"Investment: {pnl_data['investment']:.2f} USDT\n"
                           f"% Investment: {pnl_data['pnl_percent_investment']:.2f}%\n"
                           f"% Total Balance: {pnl_data['pnl_percent_balance']:.2f}%")
            _send_discord_notification(discord_msg)

        logger.info(f"Close order placed: {close_order}")
        return {
            "status": "success",
            "close_order": close_order,
            "closed_quantity": position_qty,
            "pnl": pnl_data["pnl"],
            "investment": pnl_data["investment"],
            "pnl_percent_investment": pnl_data["pnl_percent_investment"],
            "pnl_percent_balance": pnl_data["pnl_percent_balance"]
        }
    except ClientError as e:
        logger.error(f"ClientError while closing position: {e.error_message}")
        return {"status": "error", "message": str(e.error_message)}
    except Exception as e:
        logger.error(f"Unexpected error while closing position: {str(e)}")
        return {"status": "error", "message": str(e)}


def clear_all_symbol_orders(client: UMFutures, symbol: str):
    logger.info(f"START: clear_all_symbol_orders - symbol: {symbol}")
    try:
        client.cancel_open_orders(symbol=symbol)
        logger.info("Open orders cancelled successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to cancel orders: {str(e)}")
        return False


def take_profit_partially(client: UMFutures, symbol: str, leverage: int) -> Dict:
    logger.info(f"START: take_profit_partially - symbol: {symbol}")
    try:
        position_info = client.get_position_risk(symbol=symbol)
        position = next((pos for pos in position_info if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0),
                        None)

        if not position:
            logger.info(f"No open position found for {symbol}")
            return {"status": "error", "message": "No open position found"}

        current_price = float(client.ticker_price(symbol=symbol)["price"])
        entry_price = float(position["entryPrice"])
        position_qty = abs(float(position["positionAmt"]))
        position_type = "LONG" if float(position["positionAmt"]) > 0 else "SHORT"

        partial_qty = round(position_qty * 0.5, get_exchange_info(client, symbol)[1])
        new_stop_loss = get_rounded_price(client, symbol, (entry_price + current_price) / 2)
        take_profit_price = (current_price + abs(entry_price - current_price) * 20
                             if position_type == "LONG"
                             else current_price - abs(entry_price - current_price) * 20)

        side = "SELL" if position_type == "LONG" else "BUY"
        partial_order = place_market_order(client, symbol, side, leverage, quantity=partial_qty)
        if not partial_order:
            logger.error("Failed to place partial profit order")
            return {"status": "error", "message": "Partial profit order failed"}

        # Calculate PNL for partial close
        pnl_data = _calculate_pnl(client, symbol, position_type, current_price, partial_qty)

        # Send Discord notification
        discord_msg = (
            f"-------------------------TAKE PARTIALLY PROFIT POSITION---------------------------------\n"
            f"Partial Take Profit - {symbol} ({position_type})\n"
            f"PNL: {pnl_data['pnl']:.2f} USDT\n"
            f"Investment: {pnl_data['investment']:.2f} USDT\n"
            f"% Investment: {pnl_data['pnl_percent_investment']:.2f}%\n"
            f"% Total Balance: {pnl_data['pnl_percent_balance']:.2f}%")
        _send_discord_notification(discord_msg)

        if not clear_all_symbol_orders(client, symbol):
            logger.error(f"Failed to cancel existing orders for {symbol}")

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
            "market_price": current_price,
            "pnl": pnl_data["pnl"],
            "investment": pnl_data["investment"],
            "pnl_percent_investment": pnl_data["pnl_percent_investment"],
            "pnl_percent_balance": pnl_data["pnl_percent_balance"]
        }
        logger.info(f"Partial profit taken successfully: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in take_profit_partially: {str(e)}")
        return {"status": "error", "message": str(e)}
