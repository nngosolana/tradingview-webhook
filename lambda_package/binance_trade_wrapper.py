import json
import logging
import os
from decimal import Decimal
from typing import Union, Optional

import boto3
from binance.um_futures import UMFutures
from models import Position

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_binance_client():
    logger.info("START: get_binance_client")
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
        logger.info("END: get_binance_client - Successfully created UMFutures client")
        return client
    except Exception as e:
        logger.error(f"Failed to retrieve Binance API keys: {str(e)}")
        raise ValueError("Could not retrieve Binance API credentials.")


def round_step_size(quantity: Union[float, Decimal], step_size: Union[float, Decimal]) -> float:
    quantity = Decimal(str(quantity))
    return float(quantity - quantity % Decimal(str(step_size)))


def get_tick_size(client: UMFutures, symbol: str) -> float:
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            return float(sym['filters'][0]['tickSize'])
    return 0.0


def get_rounded_price(client: UMFutures, symbol: str, price: float) -> float:
    return round_step_size(price, get_tick_size(client, symbol))


def get_exchange_info(client: UMFutures, symbol: str):
    exchange_info = client.exchange_info()
    for sym in exchange_info['symbols']:
        if sym['symbol'] == symbol:
            return sym['pricePrecision'], sym['quantityPrecision']
    return None, None


def place_order(client: UMFutures, symbol: str, side: str, order_type: str, price: Optional[float] = None,
                quantity: Optional[float] = None, close_position=False):
    logger.info(f"START: place_order - symbol: {symbol}, side: {side}, order_type: {order_type}")
    try:
        order_params = {'symbol': symbol, 'side': side, 'type': order_type, 'closePosition': close_position}
        if price:
            order_params['stopPrice'] = str(get_rounded_price(client, symbol, price))
        if quantity:
            _, quantity_precision = get_exchange_info(client, symbol)
            order_params['quantity'] = str(round(quantity, quantity_precision))
        result = client.new_order(**order_params)
        logger.info(f"END: place_order - Order placed: {result}")
        return result
    except Exception as e:
        logger.error(f"Order placement failed: {e}")
        return None


def place_market_order(client: UMFutures, symbol: str, side: str, leverage: int, quantity: Optional[float] = None):
    logger.info(f"START: place_market_order - symbol: {symbol}, side: {side}, leverage: {leverage}")
    try:
        _, quantity_precision = get_exchange_info(client, symbol)
        quantity = round(quantity, quantity_precision)
        result = place_order(client, symbol, side, 'MARKET', quantity=quantity)
        logger.info(f"END: place_market_order - Result: {result}")
        return result
    except Exception as e:
        logger.error(f"Market order failed: {e}")
        return None


def fetch_all_positions(client: UMFutures, symbol: str) -> list['Position']:
    logger.info(f"Fetching all positions for {symbol}")
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
                maintMargin=pos["maintMargin"],
                positionInitialMargin=pos["positionInitialMargin"],
                openOrderInitialMargin=pos["openOrderInitialMargin"],
                adl=pos["adl"], bidNotional=pos["bidNotional"], askNotional=pos["askNotional"],
                updateTime=pos["updateTime"],
                position_type="LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            )
            positions.append(position)
    return positions
