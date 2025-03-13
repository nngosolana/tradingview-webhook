import logging

from binance.error import ClientError
from binance.um_futures import UMFutures
from binance_trade_wrapper import get_exchange_info
from config import ORDER_MAX_LIMIT_PERCENTAGE, INVESTMENT_PERCENTAGE, LEVERAGE

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)


def get_current_balance_in_usdt(client: UMFutures) -> float:
    """Get the total USDT balance including unrealized PNL and margin."""
    logging.info("START: get_current_balance_in_usdt")
    try:
        account_info = client.account()
        total_balance = float(account_info["totalWalletBalance"])  # Total wallet balance including unrealized PNL
        logging.info(f"Total USDT balance (including PNL): {total_balance}")
        logging.info("END: get_current_balance_in_usdt - Success")
        return total_balance
    except ClientError as error:
        logging.error(f"ClientError - {error.error_message}")
        return 0.0


def calculate_sl_tp_prices(client: UMFutures, symbol: str, position_type: str,
                           investment_percentage: float, max_loss_percentage: float,
                           risk_reward_ratio: str, leverage: float) -> dict:
    """Calculate SL/TP prices based on total balance with a max limit."""
    logging.info("START: calculate_sl_tp_prices")
    logging.info(f"Parameters - symbol: {symbol}, position_type: {position_type}, "
                 f"investment_percentage: {investment_percentage}, "
                 f"max_loss_percentage: {max_loss_percentage}, "
                 f"risk_reward_ratio: {risk_reward_ratio}, leverage: {leverage}")

    try:
        risk, reward = map(float, risk_reward_ratio.split(':'))
        rr_multiplier = reward / risk

        ticker = client.ticker_price(symbol=symbol)
        market_price = float(ticker["price"])
        price_precision, quantity_precision = get_exchange_info(client, symbol)

        total_balance = get_current_balance_in_usdt(client)
        max_investment_amount = total_balance * (ORDER_MAX_LIMIT_PERCENTAGE / 100)  # Cap at ORDER_MAX_LIMIT_PERCENTAGE
        investment_amount = min(total_balance * (investment_percentage / 100), max_investment_amount)
        position_value = investment_amount * leverage

        max_loss_usd = total_balance * (max_loss_percentage / 100)
        max_profit_usd = max_loss_usd * rr_multiplier

        quantity = round(position_value / market_price, quantity_precision)
        price_diff = max_loss_usd / quantity

        if position_type == "LONG":
            stop_loss_price = market_price - price_diff
            take_profit_price = market_price + (price_diff * rr_multiplier)
        else:  # SHORT
            stop_loss_price = market_price + price_diff
            take_profit_price = market_price - (price_diff * rr_multiplier)

        stop_loss_price = round(stop_loss_price, price_precision)
        take_profit_price = round(take_profit_price, price_precision)

        sl_pnl = quantity * abs(market_price - stop_loss_price) * (-1 if position_type == "LONG" else 1)
        tp_pnl = quantity * abs(market_price - take_profit_price) * (1 if position_type == "LONG" else -1)

        logging.info(f"Current market price: {market_price}")
        logging.info(f"Total balance: {total_balance}")
        logging.info(f"Max investment amount (limited to {ORDER_MAX_LIMIT_PERCENTAGE}%): {max_investment_amount}")
        logging.info(f"Investment amount: {investment_amount}")
        logging.info(f"Position value with leverage: {position_value}")
        logging.info(f"Quantity: {quantity}")
        logging.info(f"Max loss USD: {max_loss_usd}")
        logging.info(f"Max profit USD: {max_profit_usd}")
        logging.info(f"Price diff: {price_diff}")
        logging.info(f"Stop loss price: {stop_loss_price}")
        logging.info(f"Take profit price: {take_profit_price}")
        logging.info(f"Expected SL PNL: {sl_pnl}")
        logging.info(f"Expected TP PNL: {tp_pnl}")
        logging.info("END: calculate_sl_tp_prices - Success")

        return {
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "market_price": market_price,
            "investment_amount": investment_amount,
            "quantity": quantity
        }
    except Exception as e:
        logging.error(f"Error calculating SL/TP prices: {str(e)}")
        return {"status": "error", "message": str(e)}


def calculate_params_with_sl_tp_without_invest_percentage(client: UMFutures, symbol: str, position_type: str,
                                                          stop_loss_price: float, take_profit_price: float,
                                                          investment_percentage: float, leverage: float) -> dict:
    """Calculate position parameters using total balance with a max limit and provided SL/TP."""
    logging.info("START: calculate_params_with_sl_tp_without_invest_percentage")
    logging.info(f"Parameters - symbol: {symbol}, position_type: {position_type}, "
                 f"stop_loss_price: {stop_loss_price}, take_profit_price: {take_profit_price}, "
                 f"investment_percentage: {investment_percentage}, leverage: {leverage}")

    try:
        ticker = client.ticker_price(symbol=symbol)
        market_price = float(ticker["price"])
        price_precision, quantity_precision = get_exchange_info(client, symbol)

        total_balance = get_current_balance_in_usdt(client)
        max_investment_amount = total_balance * (ORDER_MAX_LIMIT_PERCENTAGE / 100)  # Cap at ORDER_MAX_LIMIT_PERCENTAGE
        investment_amount = min(total_balance * (investment_percentage / 100), max_investment_amount)
        position_value = investment_amount * leverage

        # Calculate quantity based on position value and market price
        quantity = round(position_value / market_price, quantity_precision)

        # Round provided SL/TP prices to match precision
        stop_loss_price = round(stop_loss_price, price_precision)
        take_profit_price = round(take_profit_price, price_precision)

        # Calculate PNL for validation
        sl_pnl = quantity * abs(market_price - stop_loss_price) * (-1 if position_type == "LONG" else 1)
        tp_pnl = quantity * abs(market_price - take_profit_price) * (1 if position_type == "LONG" else -1)

        # Validate that investment respects the max limit
        if investment_amount > max_investment_amount:
            logging.warning(
                f"Investment amount {investment_amount} exceeds max limit {max_investment_amount}. Capping it.")
            investment_amount = max_investment_amount
            position_value = investment_amount * leverage
            quantity = round(position_value / market_price, quantity_precision)
            sl_pnl = quantity * abs(market_price - stop_loss_price) * (-1 if position_type == "LONG" else 1)
            tp_pnl = quantity * abs(market_price - take_profit_price) * (1 if position_type == "LONG" else -1)

        logging.info(f"Current market price: {market_price}")
        logging.info(f"Total balance: {total_balance}")
        logging.info(f"Max investment amount (limited to {ORDER_MAX_LIMIT_PERCENTAGE}%): {max_investment_amount}")
        logging.info(f"Investment amount: {investment_amount}")
        logging.info(f"Position value with leverage: {position_value}")
        logging.info(f"Quantity: {quantity}")
        logging.info(f"Stop loss price: {stop_loss_price}")
        logging.info(f"Take profit price: {take_profit_price}")
        logging.info(f"Expected SL PNL: {sl_pnl}")
        logging.info(f"Expected TP PNL: {tp_pnl}")
        logging.info("END: calculate_params_with_sl_tp_without_invest_percentage - Success")

        return {
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "market_price": market_price,
            "investment_amount": investment_amount,
            "quantity": quantity
        }
    except Exception as e:
        logging.error(f"Error calculating parameters: {str(e)}")
        return {"status": "error", "message": str(e)}
