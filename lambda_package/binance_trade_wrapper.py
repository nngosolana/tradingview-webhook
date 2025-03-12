import logging
from binance.um_futures import UMFutures
from decimal import Decimal
from typing import Union, Optional, Dict, List

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

def round_step_size(quantity: Union[float, Decimal], step_size: Union[float, Decimal]) -> float:
    logging.info(f"START: round_step_size - quantity: {quantity}, step_size: {step_size}")
    quantity = Decimal(str(quantity))
    result = float(quantity - quantity % Decimal(str(step_size)))
    logging.info(f"END: round_step_size - Result: {result}")
    return result


def get_tick_size(client: UMFutures, symbol: str) -> float:
    logging.info(f"START: get_tick_size - symbol: {symbol}")
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            result = float(sym['filters'][0]['tickSize'])
            logging.info(f"END: get_tick_size - Tick size: {result}")
            return result
    logging.warning(f"END: get_tick_size - No tick size found for {symbol}")
    return 0.0


def get_rounded_price(client: UMFutures, symbol: str, price: float) -> float:
    logging.info(f"START: get_rounded_price - symbol: {symbol}, price: {price}")
    result = round_step_size(price, get_tick_size(client, symbol))
    logging.info(f"END: get_rounded_price - Rounded price: {result}")
    return result


def get_exchange_info(client: UMFutures, symbol: str):
    logging.info(f"START: get_exchange_info - symbol: {symbol}")
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            result = (sym['pricePrecision'], sym['quantityPrecision'])
            logging.info(f"END: get_exchange_info - Price precision: {result[0]}, Quantity precision: {result[1]}")
            return result
    logging.warning(f"END: get_exchange_info - No info found for {symbol}")
    return None, None


def place_order(client: UMFutures, symbol: str, side: str, order_type: str, price: Optional[float] = None,
                quantity: Optional[float] = None, position_side: Optional[str] = None, close_position=False):
    logging.info(f"START: place_order - symbol: {symbol}, side: {side}, order_type: {order_type}")
    try:
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'positionSide': position_side if position_side else 'BOTH',
            'closePosition': close_position
        }
        if price:
            order_params['stopPrice'] = str(get_rounded_price(client, symbol, price))
        if quantity:
            _, quantity_precision = get_exchange_info(client, symbol)
            order_params['quantity'] = str(round(quantity, quantity_precision))
        result = client.new_order(**order_params)
        logging.info(f"END: place_order - Order placed: {result}")
        return result
    except Exception as e:
        logging.error(f"Order placement failed: {e}")
        return None


def place_market_order(client: UMFutures, symbol: str, side: str, leverage: int,
                       tradingAmountUsdt: float = None, quantity: Optional[float] = None,
                       position_side: Optional[str] = None):
    logging.info(f"START: place_market_order - symbol: {symbol}, side: {side}, leverage: {leverage}")
    try:
        price_precision, quantity_precision = get_exchange_info(client, symbol)
        market_price = float(client.ticker_price(symbol=symbol)['price'])

        if quantity is None and tradingAmountUsdt is not None:
            logging.info(f"Calculating quantity from USDT: {tradingAmountUsdt}, leverage: {leverage}")
            quantity = round((tradingAmountUsdt * leverage) / market_price, quantity_precision)
        elif quantity is not None:
            quantity = round(quantity, quantity_precision)
        else:
            raise ValueError("Either tradingAmountUsdt or quantity must be provided")

        logging.info(f"Market price: {market_price}")
        logging.info(f"Final quantity: {quantity}")
        result = place_order(client, symbol, side, 'MARKET', quantity=quantity, position_side=position_side)
        logging.info(f"END: place_market_order - Result: {result}")
        return result
    except Exception as e:
        logging.error(f"Market order failed: {e}")
        return None
