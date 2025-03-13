import logging

from binance.error import ClientError
from binance.um_futures import UMFutures
from binance_trade_wrapper import get_exchange_info

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

def get_current_balance_in_usdt(client: UMFutures):
    logging.info("START: get_usdt_balance")
    try:
        balance_data = client.balance()
        usdt_balance = next((item for item in balance_data if item["asset"] == "USDT"), None)
        balance = float(usdt_balance["availableBalance"]) if usdt_balance else 0.0
        logging.info(f"USDT available balance: {balance}")
        logging.info("END: get_usdt_balance - Success")
        return balance
    except ClientError as error:
        logging.error(f"ClientError - {error.error_message}")
        return 0.0


def calculate_sl_tp_prices(client: UMFutures, symbol: str, position_type: str,
                           investment_percentage: float, max_loss_percentage: float,
                           risk_reward_ratio: str, leverage: float):
    logging.info("START: calculate_sl_tp_prices")
    logging.info(f"Parameters - symbol: {symbol}, position_type: {position_type}, "
                 f"investment_percentage: {investment_percentage}, "
                 f"max_loss_percentage: {max_loss_percentage}, "
                 f"risk_reward_ratio: {risk_reward_ratio}")

    try:
        risk, reward = map(float, risk_reward_ratio.split(':'))
        rr_multiplier = reward / risk

        ticker = client.ticker_price(symbol=symbol)
        market_price = float(ticker["price"])
        price_precision, quantity_precision = get_exchange_info(client, symbol)

        usdt_balance = get_current_balance_in_usdt(client)
        investment_amount = usdt_balance * (investment_percentage / 100)
        position_value = investment_amount * leverage

        max_loss_usd = usdt_balance * (max_loss_percentage / 100)
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
        logging.info(f"Total balance: {usdt_balance}")
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


def calculate_params_with_sl_tp_wihtout_invest_percentage(client: UMFutures, symbol: str, position_type: str,
                                                          stop_loss_price: float, take_profit_price: float,
                                                          investment_percentage: float, leverage: float):
    logging.info("START: calculate_params_with_sl_percentage")
    logging.info(f"Parameters - symbol: {symbol}, position_type: {position_type}, "
                 f"stop_loss_price: {stop_loss_price}, take_profit_price: {take_profit_price}")

    try:
        ticker = client.ticker_price(symbol=symbol)
        market_price = float(ticker["price"])
        price_precision, quantity_precision = get_exchange_info(client, symbol)

        usdt_balance = get_current_balance_in_usdt(client)

        # Calculate SL price based on percentage of market price
        if position_type == "LONG":
            price_diff = market_price - stop_loss_price
        else:  # SHORT
            price_diff = stop_loss_price - market_price

        stop_loss_price = round(stop_loss_price, price_precision)
        take_profit_price = round(take_profit_price, price_precision)

        # Calculate max loss in USD based on SL price
        max_loss_usd_per_unit = abs(market_price - stop_loss_price)
        # Assume quantity is derived from a fixed risk amount (e.g., 1% of balance as max loss)
        max_loss_usd = usdt_balance * investment_percentage / 100  # 1% of balance as example risk
        quantity = round(max_loss_usd / max_loss_usd_per_unit, quantity_precision)
        position_value = quantity * market_price
        investment_amount = position_value / leverage

        sl_pnl = quantity * abs(market_price - stop_loss_price) * (-1 if position_type == "LONG" else 1)
        tp_pnl = quantity * abs(market_price - take_profit_price) * (1 if position_type == "LONG" else -1)

        logging.info(f"Current market price: {market_price}")
        logging.info(f"Total balance: {usdt_balance}")
        logging.info(f"Investment amount: {investment_amount}")
        logging.info(f"Position value with leverage: {position_value}")
        logging.info(f"Quantity: {quantity}")
        logging.info(f"Max loss USD: {max_loss_usd}")
        logging.info(f"Price diff: {price_diff}")
        logging.info(f"Stop loss price: {stop_loss_price}")
        logging.info(f"Take profit price: {take_profit_price}")
        logging.info(f"Expected SL PNL: {sl_pnl}")
        logging.info(f"Expected TP PNL: {tp_pnl}")
        logging.info("END: calculate_params_with_sl_percentage - Success")

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
