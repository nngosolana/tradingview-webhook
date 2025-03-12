import json
import logging
import os
from typing import Dict, Optional
import pandas as pd
import boto3
from binance.um_futures import UMFutures

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

# Initialize AWS Lambda client
lambda_client = boto3.client('lambda')

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

def calculate_macd(client: UMFutures, symbol: str, interval: str = "5m",
                   fast_length: int = 18, slow_length: int = 39, signal_length: int = 15) -> Dict:
    """Calculate MACD with given parameters using Binance 5m klines."""
    try:
        klines = client.klines(symbol=symbol, interval=interval, limit=100)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignored'
        ])
        logging.info(f"Latest Close Price: {df['close'].iloc[-1]}")
        df['close'] = df['close'].astype(float)
        ema_fast = df['close'].ewm(span=fast_length, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow_length, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_length, adjust=False).mean()
        histogram = macd_line - signal_line
        latest_macd = macd_line.iloc[-1]
        latest_signal = signal_line.iloc[-1]
        latest_histogram = histogram.iloc[-1]
        logging.info(f"MACD Calculated - MACD: {latest_macd}, Signal: {latest_signal}, Histogram: {latest_histogram}")
        return {
            "macd": latest_macd,
            "signal": latest_signal,
            "histogram": latest_histogram
        }
    except Exception as e:
        logging.error(f"Error calculating MACD: {str(e)}")
        return {"macd": None, "signal": None, "histogram": None}

def invoke_trading_lambda(symbol: str, action: str, position_type: str = None,
                          take_profit: float = None, stop_loss: float = None, close_price: float = None):
    """Invoke the trading Lambda function."""
    try:
        payload = {"action": action, "symbol": symbol}

        if action in ["open_long_sl_tp_without_investment", "open_short_sl_tp_without_investment"]:
            payload.update({
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "investment_percentage": 3.0,  # Example value
                "leverage": 10  # Example value
            })

        response = lambda_client.invoke(
            FunctionName='binance-trading-bot',  # Replace with your second Lambda's name
            InvocationType='Event',  # Asynchronous invocation
            Payload=json.dumps(payload)
        )
        logging.info(f"Invoked trading Lambda with payload: {payload}, response: {response}")
        return True
    except Exception as e:
        logging.error(f"Failed to invoke trading Lambda: {str(e)}")
        return False

def lambda_handler(event: Dict, context: object) -> Dict:
    """Handle LuxAlgo webhook and process trading signals with MACD."""
    logging.info("START: lambda_handler")
    try:
        # Handle both webhook (body as string) and direct events (body as dict)
        if 'body' in event:
            body_str = event.get('body', '')
            if isinstance(body_str, str) and body_str:
                body = json.loads(body_str)
            else:
                body = body_str  # If body is already a dict or empty
        else:
            body = event  # Direct event, no 'body' key

        logging.info(f"Parsed Webhook Body: {body}")

        symbol = body.get('ticker')
        exchange = body.get('exchange')
        interval = body.get('interval')
        timenow = body.get('timenow')
        time = body.get('time')
        open_price = float(body.get('open', 0)) if body.get('open') is not None else 0.0
        close_price = float(body.get('close', 0)) if body.get('close') is not None else 0.0
        high_price = float(body.get('high', 0)) if body.get('high') is not None else 0.0
        low_price = float(body.get('low', 0)) if body.get('low') is not None else 0.0
        volume = float(body.get('volume', 0)) if body.get('volume') is not None else 0.0

        bullish = int(body.get('Bullish', 0)) if body.get('Bullish') is not None else 0
        bullish_plus = int(body.get('Bullish+', 0)) if body.get('Bullish+') is not None else 0
        bearish = int(body.get('Bearish', 0)) if body.get('Bearish') is not None else 0
        bearish_plus = int(body.get('Bearish+', 0)) if body.get('Bearish+') is not None else 0
        bullish_exit = body.get('BullishExit') is not None and body.get('BullishExit') != 0
        bearish_exit = body.get('BearishExit') is not None and body.get('BearishExit') != 0

        take_profit = float(body.get('Take Profit', 0)) if body.get('Take Profit') is not None else None
        stop_loss = float(body.get('Stop Loss', 0)) if body.get('Stop Loss') is not None else None

        premium_bottom = float(body.get('Premium Bottom', 0)) if body.get('Premium Bottom') is not None and 'plot' not in str(body.get('Premium Bottom', '')) else None
        trend_strength = float(body.get('Trend Strength', 0)) if body.get('Trend Strength') is not None else 0.0

        trend_tracer = float(body.get('Trend_Tracer', 0)) if body.get('Trend_Tracer') is not None else None
        trend_catcher = float(body.get('Trend_Catcher', 0)) if body.get('Trend_Catcher') is not None else None
        smart_trail = float(body.get('Smart_Trail', 0)) if body.get('Smart_Trail') is not None else None
        smart_trail_extremity = float(body.get('Smart_Trail_Extremity', 0)) if body.get('Smart_Trail_Extremity') is not None else None

        rz_r3_band = float(body.get('RZ_R3_Band', 0)) if body.get('RZ_R3_Band') is not None else None
        rz_r2_band = float(body.get('RZ_R2_Band', 0)) if body.get('RZ_R2_Band') is not None else None
        rz_r1_band = float(body.get('RZ_R1_Band', 0)) if body.get('RZ_R1_Band') is not None else None
        reversal_zones_avg = float(body.get('Reversal_Zones_Average', 0)) if body.get('Reversal_Zones_Average') is not None else None
        rz_s1_band = float(body.get('RZ_S1_Band', 0)) if body.get('RZ_S1_Band') is not None else None
        rz_s2_band = float(body.get('RZ_S2_Band', 0)) if body.get('RZ_S2_Band') is not None else None

        position_type = None
        if bullish or bullish_plus:
            position_type = "LONG"
        elif bearish or bearish_plus:
            position_type = "SHORT"

        logging.info(f"Received Signal - Symbol: {symbol}, Position: {position_type}, "
                     f"Price: {close_price}, TP: {take_profit}, SL: {stop_loss}, "
                     f"Trend Strength: {trend_strength}")

        client = get_binance_client()
        if not client:
            raise Exception("Failed to initialize Binance client")

        message = 'Webhook received successfully, no trade executed'
        macd_result = None

        if position_type:
            macd_result = calculate_macd(client, symbol, interval="5m",
                                         fast_length=18, slow_length=39, signal_length=15)
            logging.info(f"MACD Results: {macd_result}")
            logging.info(f"MACD Results - macd: {macd_result['macd'] * 100}")
            logging.info(f"MACD Results - signal: {macd_result['signal'] * 100}")
            logging.info(f"MACD Results - histogram: {macd_result['histogram'] * 100}")
            logging.info(f"------------------------------------------------------------------------")

            if position_type == "LONG" and macd_result['histogram'] > 0:
                logging.info(f"TRIGGER LONG SETUP")
                message = "LONG SETUP - Trade Triggered"
                invoke_trading_lambda(symbol, "open_long_sl_tp_without_investment",
                                      position_type, take_profit, stop_loss, close_price)
            elif position_type == "SHORT" and macd_result['histogram'] < 0:
                logging.info(f"TRIGGER SHORT SETUP")
                message = "SHORT SETUP - Trade Triggered"
                invoke_trading_lambda(symbol, "open_short_sl_tp_without_investment",
                                      position_type, take_profit, stop_loss, close_price)
            else:
                logging.info(f"NON-CONFIRM SIGNAL SETUP")
                message = "NON-CONFIRM SIGNAL SETUP - Take Profit Partially"
                invoke_trading_lambda(symbol, "take_profit_partially")
            logging.info(f"------------------------------------------------------------------------")

        if bearish_exit or bullish_exit:
            logging.info(f"------------------------------------------------------------------------")
            logging.info(f"TRIGGER EXIT SETUP")
            message = "EXIT SETUP - Partial Take Profit Triggered"
            invoke_trading_lambda(symbol, "take_profit_partially")
            logging.info(f"------------------------------------------------------------------------")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "macd": macd_result,
                "signal": {
                    "symbol": symbol,
                    "position_type": position_type,
                    "close_price": close_price,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss
                }
            })
        }

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }

def main():
    # JSON equivalent of the previous YAML input
    json_input = {
        "ticker": "DOGEUSDT",
        "exchange": "BINANCE",
        "interval": "1",
        "timenow": "2025-03-12T03:18:01Z",
        "time": "2025-03-12T03:17:00Z",
        "open": "0.16317",
        "close": "0.16299",
        "high": "0.16319",
        "volume": "196461",
        "low": "0.16299",
        "Bullish": "0",
        "Bullish+": "0",
        "Bearish": "0",
        "Bearish+": "0",
        "BullishExit": None,
        "BearishExit": None,
        "Take Profit": "0.1620825100139974",
        "Stop Loss": "0.1638974899860026",
        "Premium Bottom": "{{plot(Premium Bottom)}}",
        "Trend Strength": "73.20487545072551",
        "Trend_Tracer": "0.163700210962177",
        "Trend_Catcher": "0.16346498717",
        "Smart_Trail": "0.1644178133377382",
        "Smart_Trail_Extremity": "0.1640608600033037",
        "RZ_R3_Band": "0.1669824463710614",
        "RZ_R2_Band": "0.166080965595846",
        "RZ_R1_Band": "0.1651794848206306",
        "Reversal_Zones_Average": "0.1637987314119056",
        "RZ_S1_Band": "0.1624179780031806",
        "RZ_S2_Band": "0.1615164972279652"
    }

    # Simulate Lambda event with JSON body as a string
    event = {"body": json.dumps(json_input)}
    response = lambda_handler(event, None)
    print("Response:")
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()