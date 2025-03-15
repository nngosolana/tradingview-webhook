import json
import logging
import os
from decimal import Decimal
from typing import Union, Optional

import boto3
from binance.um_futures import UMFutures
from models import Position

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)


def get_binance_client():
    logging.info("START: get_binance_client")
    try:
        is_lambda = os.getenv("AWS_LAMBDA_FUNCTION_NAME") is not None
        if is_lambda:
            secret_name = "binance_api_keys"
            region_name = "ap-southeast-1"
            client = boto3.client("secretsmanager", region_name=region_name)
            response = client.get_secret_value(SecretId=secret_name)
            secret = json.loads(response["SecretString"])
            api_key = secret["BINANCE_API_KEY"]
            api_secret = secret["BINANCE_API_SECRET"]
        else:
            api_key = os.getenv("BINANCE_API_KEY")
            api_secret = os.getenv("BINANCE_API_SECRET")
            if not api_key or not api_secret:
                raise ValueError("Missing Binance API credentials")
        client = UMFutures(key=api_key, secret=api_secret)
        logging.info("END: get_binance_client - Successfully created UMFutures client")
        return client
    except Exception as e:
        logging.error(f"Failed to retrieve Binance API keys: {str(e)}")
        raise ValueError("Could not retrieve Binance API credentials.")


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
def fetch_all_positions(client: UMFutures, symbol: str) -> list['Position']:
    """
    Fetch all existing positions for a given symbol and include position type.

    Args:
        symbol (str): The trading pair symbol (e.g., "XRPUSDT")

    Returns:
        list[Position]: List of Position objects
    """
    logging.info(f"Fetching all positions for {symbol}")
    position_info = client.get_position_risk(symbol=symbol)
    logging.info(f"Position info: {position_info}")

    positions = []
    for pos in position_info:
        if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0:
            position_type = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            position = Position(
                symbol=pos["symbol"],
                positionSide=pos["positionSide"],
                positionAmt=pos["positionAmt"],
                entryPrice=pos["entryPrice"],
                breakEvenPrice=pos["breakEvenPrice"],
                markPrice=pos["markPrice"],
                unRealizedProfit=pos["unRealizedProfit"],
                liquidationPrice=pos["liquidationPrice"],
                isolatedMargin=pos["isolatedMargin"],
                notional=pos["notional"],
                marginAsset=pos["marginAsset"],
                isolatedWallet=pos["isolatedWallet"],
                initialMargin=pos["initialMargin"],
                maintMargin=pos["maintMargin"],
                positionInitialMargin=pos["positionInitialMargin"],
                openOrderInitialMargin=pos["openOrderInitialMargin"],
                adl=pos["adl"],
                bidNotional=pos["bidNotional"],
                askNotional=pos["askNotional"],
                updateTime=pos["updateTime"],
                position_type=position_type
            )
            positions.append(position)
            logging.info(f"Found position: {position_type}, Quantity: {pos['positionAmt']}")

    if not positions:
        logging.info(f"No positions found for {symbol}")

    return positions
