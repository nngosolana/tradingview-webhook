import json
import os
from decimal import Decimal
from typing import Tuple, Optional, Union, List
import time

import boto3
from binance.um_futures import UMFutures
from models import Position

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_binance_client() -> UMFutures:
    logger.info("START: get_binance_client - Input: None")
    try:
        is_lambda = os.getenv("AWS_LAMBDA_FUNCTION_NAME") is not None
        logger.debug(f"Checking environment: is_lambda={is_lambda}")
        if is_lambda:
            secret_name = "binance_api_keys"
            region_name = "ap-southeast-1"
            client = boto3.client("secretsmanager", region_name=region_name)
            response = client.get_secret_value(SecretId=secret_name)
            secret = json.loads(response["SecretString"])
            api_key = secret["BINANCE_API_KEY"]
            api_secret = secret["BINANCE_API_SECRET"]
            logger.debug(f"Retrieved keys from Secrets Manager: secret_name={secret_name}")
        else:
            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")
            logger.debug(f"Retrieved keys from env: api_key_exists={bool(api_key)}, api_secret_exists={bool(api_secret)}")
            if not api_key or not api_secret:
                raise ValueError("Missing Binance API credentials")
        client = UMFutures(key=api_key, secret=api_secret)
        logger.info("END: get_binance_client - Output: client initialized")
        return client
    except Exception as e:
        logger.error(f"Failed to retrieve Binance API keys: {str(e)}")
        logger.info(f"END: get_binance_client - Output (error): exception={str(e)}")
        raise ValueError("Could not retrieve Binance API credentials.")

def set_hedge_mode(client: UMFutures, enable_hedge: bool = True) -> bool:
    """Enable or disable Hedge Mode for the account."""
    logger.info(f"START: set_hedge_mode - Input: enable_hedge={enable_hedge}")
    try:
        # Check current position mode
        current_mode = client.get_position_mode()
        is_hedge_mode = current_mode["dualSidePosition"]
        logger.debug(f"Current position mode: is_hedge_mode={is_hedge_mode}")

        # If already in the desired mode, no change needed
        if is_hedge_mode == enable_hedge:
            logger.info(f"Hedge Mode already set to: {enable_hedge}")
            logger.info("END: set_hedge_mode - Output: success=True (no change needed)")
            return True

        # Change to Hedge Mode (or One-Way Mode if enable_hedge=False)
        response = client.change_position_mode(dualSidePosition=enable_hedge)
        logger.info(f"Position mode changed: response={json.dumps(response)}")
        logger.info("END: set_hedge_mode - Output: success=True")
        return True
    except Exception as e:
        logger.error(f"Failed to set Hedge Mode: {str(e)}")
        logger.info(f"END: set_hedge_mode - Output (error): success=False, exception={str(e)}")
        return False

def wait_for_order_to_fill(client: UMFutures, symbol: str, order_id: int, timeout: int = 30) -> bool:
    """Wait for an order to be filled, with a timeout in seconds."""
    logger.info(f"START: wait_for_order_to_fill - Input: symbol={symbol}, order_id={order_id}, timeout={timeout}")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Use query_order instead of get_order for Futures API
            order = client.query_order(symbol=symbol, orderId=order_id)
            status = order["status"]
            logger.debug(f"Order status: order_id={order_id}, status={status}")
            if status == "FILLED":
                logger.info(f"Order filled: order_id={order_id}")
                logger.info("END: wait_for_order_to_fill - Output: success=True")
                return True
            elif status in ["CANCELED", "REJECTED", "EXPIRED"]:
                logger.error(f"Order failed: order_id={order_id}, status={status}")
                logger.info("END: wait_for_order_to_fill - Output: success=False (order failed)")
                return False
            time.sleep(1)  # Wait 1 second before checking again
        except Exception as e:
            logger.error(f"Error checking order status: {str(e)}")
            time.sleep(1)
    logger.error(f"Timeout waiting for order to fill: order_id={order_id}")
    logger.info("END: wait_for_order_to_fill - Output: success=False (timeout)")
    return False

def round_step_size(quantity: Union[float, Decimal], step_size: Union[float, Decimal]) -> float:
    logger.info(f"START: round_step_size - Input: quantity={quantity}, step_size={step_size}")
    quantity = Decimal(str(quantity))
    step_size = Decimal(str(step_size))
    result = float(quantity - quantity % step_size)
    logger.info(f"END: round_step_size - Output: result={result}")
    return result

def fetch_current_price(symbol: str = "DOGEUSDT") -> float:
    logger.info(f"START: fetch_current_price - Input: symbol={symbol}")
    try:
        client = get_binance_client()
        logger.debug(f"Client initialized: client={client}")
        if not client:
            raise ValueError("Failed to initialize Binance client")
        ticker = client.ticker_price(symbol=symbol)
        price = float(ticker["price"])
        logger.info(f"END: fetch_current_price - Output: price={price}")
        return price
    except Exception as e:
        logger.error(f"Unexpected error fetching price for {symbol}: {str(e)}")
        logger.info(f"END: fetch_current_price - Output (error): price=0.0, exception={str(e)}")
        return 0.0

def get_tick_size(client: UMFutures, symbol: str) -> float:
    logger.info(f"START: get_tick_size - Input: client={client}, symbol={symbol}")
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            result = float(sym['filters'][0]['tickSize'])
            logger.info(f"END: get_tick_size - Output: result={result}")
            return result
    result = 0.0
    logger.info(f"END: get_tick_size - Output: result={result} (symbol not found)")
    return result

def get_quantity_step_size(client: UMFutures, symbol: str) -> float:
    logger.info(f"START: get_quantity_step_size - Input: client={client}, symbol={symbol}")
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            for filt in sym['filters']:
                if filt['filterType'] == 'LOT_SIZE':
                    result = float(filt['stepSize'])
                    logger.info(f"END: get_quantity_step_size - Output: result={result}")
                    return result
    result = 0.0
    logger.info(f"END: get_quantity_step_size - Output: result={result} (symbol not found)")
    return result

def get_rounded_price(client: UMFutures, symbol: str, price: float) -> float:
    logger.info(f"START: get_rounded_price - Input: client={client}, symbol={symbol}, price={price}")
    result = round_step_size(price, get_tick_size(client, symbol))
    logger.info(f"END: get_rounded_price - Output: result={result}")
    return result

def get_exchange_info(client: UMFutures, symbol: str) -> Tuple[Optional[int], Optional[int]]:
    logger.info(f"START: get_exchange_info - Input: client={client}, symbol={symbol}")
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            result = (sym['pricePrecision'], sym['quantityPrecision'])
            logger.info(f"END: get_exchange_info - Output: result={result}")
            return result
    result = (None, None)
    logger.info(f"END: get_exchange_info - Output: result={result} (symbol not found)")
    return result

def calculate_quantity(client: UMFutures, symbol: str, margin: float, leverage: float) -> float:
    logger.info(f"START: calculate_quantity - Input: client={client}, symbol={symbol}, margin={margin}, leverage={leverage}")
    try:
        current_price = fetch_current_price(symbol)
        if current_price == 0.0:
            raise ValueError("Failed to fetch current price")

        total_investment = margin * leverage
        quantity = total_investment / current_price
        step_size = get_quantity_step_size(client, symbol)
        rounded_quantity = round_step_size(quantity, step_size) if step_size > 0 else quantity

        logger.debug(f"Calculation: total_investment={total_investment}, current_price={current_price}, "
                     f"quantity={quantity}, step_size={step_size}, rounded_quantity={rounded_quantity}")
        logger.info(f"END: calculate_quantity - Output: rounded_quantity={rounded_quantity}")
        return rounded_quantity
    except Exception as e:
        logger.error(f"Quantity calculation failed: {str(e)}")
        logger.info(f"END: calculate_quantity - Output (error): rounded_quantity=0.0, exception={str(e)}")
        return 0.0

def update_leverage(client: UMFutures, symbol: str, leverage: float) -> bool:
    logger.info(f"START: update_leverage - Input: client={client}, symbol={symbol}, leverage={leverage}")
    try:
        leverage_int = int(leverage)
        response = client.change_leverage(symbol=symbol, leverage=leverage_int)
        logger.info(f"END: update_leverage - Output: success=True, response={json.dumps(response)}")
        return True
    except Exception as e:
        logger.error(f"Failed to update leverage: {str(e)}")
        logger.info(f"END: update_leverage - Output (error): success=False, exception={str(e)}")
        return False

def cancel_open_orders_by_side(client: UMFutures, symbol: str, side: str) -> bool:
    """Cancel all open limit orders for the specified symbol and side."""
    logger.info(f"START: cancel_open_orders_by_side - Input: symbol={symbol}, side={side}")
    try:
        all_orders = client.get_all_orders(symbol=symbol)
        logger.debug(f"Fetched all orders: count={len(all_orders)}")
        canceled_count = 0

        for order in all_orders:
            # Check if the order is open (NEW or PARTIALLY_FILLED) and matches the side
            if order["status"] in ["NEW", "PARTIALLY_FILLED"] and order["side"] == side.upper():
                client.cancel_order(symbol=symbol, orderId=order["orderId"])
                canceled_count += 1
                logger.debug(f"Canceled order: orderId={order['orderId']}, side={order['side']}, type={order['type']}")

        logger.info(f"END: cancel_open_orders_by_side - Output: success=True, canceled_count={canceled_count}")
        return True
    except Exception as e:
        logger.error(f"Failed to cancel open orders: {str(e)}")
        logger.info(f"END: cancel_open_orders_by_side - Output (error): success=False, exception={str(e)}")
        return False

def create_market_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float,
                        reduce_only: bool = False, close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_market_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity)
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_market_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Market order failed: {str(e)}")
        logger.info(f"END: create_market_order - Output (error): result=None, exception={str(e)}")
        return None

def create_limit_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float, price: float,
                       time_in_force: str = "GTC", reduce_only: bool = False,
                       close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_limit_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, price={price}, time_in_force={time_in_force}, "
                f"reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round price
        rounded_price = get_rounded_price(client, symbol, price)
        logger.debug(f"Rounded price: {rounded_price}")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'quantity': str(quantity),
            'price': str(rounded_price),
            'timeInForce': time_in_force
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_limit_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Limit order failed: {str(e)}")
        logger.info(f"END: create_limit_order - Output (error): result=None, exception={str(e)}")
        return None

def create_stop_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float, price: float,
                      stop_price: float, time_in_force: str = "GTC", reduce_only: bool = False,
                      close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_stop_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, price={price}, stop_price={stop_price}, time_in_force={time_in_force}, "
                f"reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round prices
        rounded_price = get_rounded_price(client, symbol, price)
        rounded_stop_price = get_rounded_price(client, symbol, stop_price)
        logger.debug(f"Rounded values: price={rounded_price}, stop_price={rounded_stop_price}")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'STOP',
            'quantity': str(quantity),
            'price': str(rounded_price),
            'stopPrice': str(rounded_stop_price),
            'timeInForce': time_in_force
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_stop_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Stop order failed: {str(e)}")
        logger.info(f"END: create_stop_order - Output (error): result=None, exception={str(e)}")
        return None

def create_stop_market_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float,
                             stop_price: float, reduce_only: bool = False,
                             close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_stop_market_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, stop_price={stop_price}, reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round stop price
        rounded_stop_price = get_rounded_price(client, symbol, stop_price)
        logger.debug(f"Rounded stop_price: {rounded_stop_price}")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'STOP_MARKET',
            'quantity': str(quantity),
            'stopPrice': str(rounded_stop_price)
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_stop_market_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Stop market order failed: {str(e)}")
        logger.info(f"END: create_stop_market_order - Output (error): result=None, exception={str(e)}")
        return None

def create_take_profit_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float, price: float,
                             stop_price: float, time_in_force: str = "GTC", reduce_only: bool = False,
                             close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_take_profit_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, price={price}, stop_price={stop_price}, time_in_force={time_in_force}, "
                f"reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round prices
        rounded_price = get_rounded_price(client, symbol, price)
        rounded_stop_price = get_rounded_price(client, symbol, stop_price)
        logger.debug(f"Rounded values: price={rounded_price}, stop_price={rounded_stop_price}")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'TAKE_PROFIT',
            'quantity': str(quantity),
            'price': str(rounded_price),
            'stopPrice': str(rounded_stop_price),
            'timeInForce': time_in_force
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_take_profit_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Take profit order failed: {str(e)}")
        logger.info(f"END: create_take_profit_order - Output (error): result=None, exception={str(e)}")
        return None

def create_take_profit_market_order(client: UMFutures, symbol: str, side: str, margin: float, leverage: float,
                                    stop_price: float, reduce_only: bool = False,
                                    close_position: bool = False) -> Optional[dict]:
    logger.info(f"START: create_take_profit_market_order - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, stop_price={stop_price}, reduce_only={reduce_only}, close_position={close_position}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round stop price
        rounded_stop_price = get_rounded_price(client, symbol, stop_price)
        logger.debug(f"Rounded stop_price: {rounded_stop_price}")

        # Prepare order parameters
        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': str(quantity),
            'stopPrice': str(rounded_stop_price)
        }

        # In Hedge Mode, specify positionSide
        if side == "BUY":
            order_params['positionSide'] = "LONG"
        elif side == "SELL":
            order_params['positionSide'] = "SHORT"

        # Include reduceOnly only if True
        if reduce_only:
            order_params['reduceOnly'] = True

        # Include closePosition only if True
        if close_position:
            order_params['closePosition'] = True

        # Place the order
        result = client.new_order(**order_params)
        logger.info(f"END: create_take_profit_market_order - Output: result={json.dumps(result)}")
        return result
    except Exception as e:
        logger.error(f"Take profit market order failed: {str(e)}")
        logger.info(f"END: create_take_profit_market_order - Output (error): result=None, exception={str(e)}")
        return None

def set_limit_take_profit_stop_loss(client: UMFutures, symbol: str, side: str, margin: float, leverage: float,
                                    take_profit_price: float, stop_loss_price: float,
                                    time_in_force: str = "GTC") -> Tuple[Optional[dict], Optional[dict]]:
    """Set take profit and stop loss as limit orders for an existing position."""
    logger.info(f"START: set_limit_take_profit_stop_loss - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, take_profit_price={take_profit_price}, stop_loss_price={stop_loss_price}, "
                f"time_in_force={time_in_force}")
    try:
        # Ensure Hedge Mode is enabled
        if not set_hedge_mode(client, enable_hedge=True):
            raise ValueError("Failed to enable Hedge Mode")

        # Set leverage
        if not update_leverage(client, symbol, leverage):
            raise ValueError(f"Failed to set leverage to {leverage} for {symbol}")

        # Calculate quantity
        quantity = calculate_quantity(client, symbol, margin, leverage)
        if quantity == 0.0:
            raise ValueError("Calculated quantity is zero")

        # Round prices
        rounded_take_profit_price = get_rounded_price(client, symbol, take_profit_price)
        rounded_stop_loss_price = get_rounded_price(client, symbol, stop_loss_price)
        logger.debug(f"Rounded values: take_profit_price={rounded_take_profit_price}, stop_loss_price={rounded_stop_loss_price}")

        # In Hedge Mode, TP/SL orders are on the opposite side, so positionSide must reflect that
        tp_sl_position_side = "SHORT" if side == "BUY" else "LONG"

        # Take Profit order
        take_profit_params = {
            'symbol': symbol,
            'side': side,
            'type': 'TAKE_PROFIT',
            'quantity': str(quantity),
            'price': str(rounded_take_profit_price),
            'stopPrice': str(rounded_take_profit_price),
            'timeInForce': time_in_force,
            'positionSide': tp_sl_position_side
        }
        take_profit_result = client.new_order(**take_profit_params)
        logger.debug(f"Take profit order placed: {json.dumps(take_profit_result)}")

        # Stop Loss order
        stop_loss_params = {
            'symbol': symbol,
            'side': side,
            'type': 'STOP',
            'quantity': str(quantity),
            'price': str(rounded_stop_loss_price),
            'stopPrice': str(rounded_stop_loss_price),
            'timeInForce': time_in_force,
            'positionSide': tp_sl_position_side
        }
        stop_loss_result = client.new_order(**stop_loss_params)
        logger.debug(f"Stop loss order placed: {json.dumps(stop_loss_result)}")

        logger.info(f"END: set_limit_take_profit_stop_loss - Output: take_profit_result={json.dumps(take_profit_result)}, "
                    f"stop_loss_result={json.dumps(stop_loss_result)}")
        return take_profit_result, stop_loss_result

    except Exception as e:
        logger.error(f"Setting take profit and stop loss failed: {str(e)}")
        logger.info(f"END: set_limit_take_profit_stop_loss - Output (error): take_profit_result=None, "
                    f"stop_loss_result=None, exception={str(e)}")
        return None, None

def execute_trade_with_tp_sl(client: UMFutures, symbol: str, side: str, margin: float, leverage: float,
                             take_profit_price: float, stop_loss_price: float,
                             order_type: str = "MARKET", limit_price: float = None) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """Execute a trade (Market or Limit) and set limit-based take profit and stop loss."""
    logger.info(f"START: execute_trade_with_tp_sl - Input: symbol={symbol}, side={side}, margin={margin}, "
                f"leverage={leverage}, take_profit_price={take_profit_price}, stop_loss_price={stop_loss_price}, "
                f"order_type={order_type}, limit_price={limit_price}")

    try:
        # Opposite side for TP/SL orders
        opposite_side = "SELL" if side == "BUY" else "BUY"

        # Place the main order
        if order_type.upper() == "MARKET":
            order_result = create_market_order(client, symbol, side, margin, leverage)
        elif order_type.upper() == "LIMIT":
            if limit_price is None:
                raise ValueError("Limit price is required for LIMIT order type")
            # Cancel all open orders for the same side before placing a new limit order
            if not cancel_open_orders_by_side(client, symbol, side):
                logger.warning(f"Failed to cancel existing {side} orders for {symbol}, proceeding with limit order")
            order_result = create_limit_order(client, symbol, side, margin, leverage, limit_price)
        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        if not order_result:
            raise ValueError(f"Failed to execute {order_type} order")

        # Wait for the main order to be filled
        order_id = order_result["orderId"]
        if not wait_for_order_to_fill(client, symbol, order_id):
            raise ValueError(f"Main order {order_id} did not fill within the timeout period")

        # Set TP/SL orders
        take_profit_result, stop_loss_result = set_limit_take_profit_stop_loss(
            client, symbol, opposite_side, margin, leverage, take_profit_price, stop_loss_price
        )

        if not take_profit_result or not stop_loss_result:
            logger.warning("One or both TP/SL orders failed to place")

        logger.info(f"END: execute_trade_with_tp_sl - Output: order_result={json.dumps(order_result)}, "
                    f"take_profit_result={json.dumps(take_profit_result)}, stop_loss_result={json.dumps(stop_loss_result)}")
        return order_result, take_profit_result, stop_loss_result

    except Exception as e:
        logger.error(f"Trade execution with TP/SL failed: {str(e)}")
        logger.info(f"END: execute_trade_with_tp_sl - Output (error): order_result=None, take_profit_result=None, "
                    f"stop_loss_result=None, exception={str(e)}")
        return None, None, None

def fetch_all_positions(client: UMFutures, symbol: str) -> List[Position]:
    logger.info(f"START: fetch_all_positions - Input: client={client}, symbol={symbol}")
    position_info = client.get_position_risk(symbol=symbol)
    positions = []
    for pos in position_info:
        if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0:
            position = Position(
                symbol=pos["symbol"], positionSide=pos["positionSide"], positionAmt=pos["positionAmt"],
                entryPrice=pos["entryPrice"], breakEvenPrice=pos["breakEvenPrice"], markPrice=pos["markPrice"],
                unRealizedProfit=pos["unRealizedProfit"], liquidationPrice=pos["liquidationPrice"],
                isolatedMargin=pos["isolatedMargin"], notional=pos["notional"], marginAsset=pos["marginAsset"],
                isolatedWallet=pos["isolatedWallet"], initialMargin=pos["initialMargin"],
                maintMargin=pos["maintMargin"], positionInitialMargin=pos["positionInitialMargin"],
                openOrderInitialMargin=pos["openOrderInitialMargin"], adl=pos["adl"],
                bidNotional=pos["bidNotional"], askNotional=pos["askNotional"], updateTime=pos["updateTime"],
                position_type="LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            )
            positions.append(position)
    logger.info(f"END: fetch_all_positions - Output: positions={[vars(p) for p in positions]}")
    return positions

def fetch_stop_take_prices(client: UMFutures, symbol: str, position_type: str,
                           position_update_time: int, position_qty: float) -> Tuple[Optional[float], Optional[float]]:
    logger.info(f"START: fetch_stop_take_prices - Input: client={client}, symbol={symbol}, "
                f"position_type={position_type}, position_update_time={position_update_time}, position_qty={position_qty}")
    stop_loss = None
    take_profit = None
    try:
        all_orders = client.get_all_orders(symbol=symbol)
        logger.debug(f"Fetched orders: count={len(all_orders)}")
        expected_side = "SELL" if position_type == "LONG" else "BUY"

        for order in all_orders:
            order_time = int(order["time"])
            order_qty = float(order["origQty"])
            is_after_update = order_time > position_update_time
            is_partial = order_qty < position_qty
            is_filled = order["status"] == "FILLED"
            is_matching_side = order["side"] == expected_side

            if is_filled and is_after_update and is_partial and is_matching_side:
                if order["type"] == "STOP":
                    stop_loss = float(order["stopPrice"])
                elif order["type"] == "TAKE_PROFIT":
                    take_profit = float(order["stopPrice"])

        logger.info(f"END: fetch_stop_take_prices - Output: stop_loss={stop_loss}, take_profit={take_profit}")
        return stop_loss, take_profit
    except Exception as e:
        logger.error(f"Failed to fetch stop/take prices: {str(e)}")
        logger.info(f"END: fetch_stop_take_prices - Output (error): stop_loss=None, take_profit=None, exception={str(e)}")
        return (None, None)