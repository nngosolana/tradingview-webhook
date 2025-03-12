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

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

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

def calculate_macd(client: UMFutures, symbol: str, interval_raw: str = "1",
                   fast_length: int = 18, slow_length: int = 39, signal_length: int = 15) -> Dict:
    """Calculate MACD with given parameters using Binance klines."""
    try:
        interval_map = {
            "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
            "60": "1h", "120": "2h", "240": "4h", "D": "1d", "1D": "1d"
        }
        interval = interval_map.get(str(interval_raw), "1m")  # Default to 1m
        logging.info(f"Mapped interval '{interval_raw}' to Binance interval '{interval}'")

        klines = client.klines(symbol=symbol, interval=interval, limit=101)  # 101 for prev histogram
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
        prev_histogram = histogram.iloc[-2]
        logging.info(f"MACD Calculated - MACD: {latest_macd}, Signal: {latest_signal}, "
                     f"Histogram: {latest_histogram}, Prev Histogram: {prev_histogram}")
        return {
            "macd": latest_macd,
            "signal": latest_signal,
            "histogram": latest_histogram,
            "prev_histogram": prev_histogram
        }
    except Exception as e:
        logging.error(f"Error calculating MACD: {str(e)}")
        return {"macd": None, "signal": None, "histogram": None, "prev_histogram": None}

def invoke_trading_lambda(symbol: str, action: str, position_type: str = None,
                          take_profit: float = None, stop_loss: float = None, close_price: float = None):
    """Invoke the trading Lambda function."""
    try:
        payload = {"action": action, "symbol": symbol}
        if action in ["open_long_sl_tp_without_investment", "open_short_sl_tp_without_investment"]:
            payload.update({
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "investment_percentage": 3.0,
                "leverage": 10
            })
        response = lambda_client.invoke(
            FunctionName='binance-trading-bot',
            InvocationType='Event',
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
        if 'body' in event:
            body_str = event.get('body', '')
            body = json.loads(body_str) if isinstance(body_str, str) and body_str else body_str
        else:
            body = event

        logging.info(f"Parsed Webhook Body: {body}")

        # Symbol Placeholders
        alert = body.get('alert', '')
        symbol = body.get('ticker')
        exchange = body.get('exchange')
        sector = body.get('sector', 'na')
        market = body.get('market', 'Crypto')

        # Time Placeholders
        tf = body.get('tf')
        bartime = body.get('bartime')
        year = body.get('year')
        month = body.get('month')
        day = body.get('day')

        # Data Placeholders (ohlcv)
        ohlcv = body.get('ohlcv', {})
        open_price = float(ohlcv.get('open', 0)) if ohlcv.get('open') is not None else 0.0
        high_price = float(ohlcv.get('high', 0)) if ohlcv.get('high') is not None else 0.0
        low_price = float(ohlcv.get('low', 0)) if ohlcv.get('low') is not None else 0.0
        close_price = float(ohlcv.get('close', 0)) if ohlcv.get('close') is not None else 0.0
        volume = float(ohlcv.get('volume', 0)) if ohlcv.get('volume') is not None else 0.0

        # Indicators Placeholders
        indicators = body.get('indicators', {})
        smart_trail = float(indicators.get('smart_trail', 0)) if indicators.get('smart_trail') is not None else None
        rz_r3 = float(indicators.get('rz_r3', 0)) if indicators.get('rz_r3') is not None else None
        rz_r2 = float(indicators.get('rz_r2', 0)) if indicators.get('rz_r2') is not None else None
        rz_r1 = float(indicators.get('rz_r1', 0)) if indicators.get('rz_r1') is not None else None
        rz_s1 = float(indicators.get('rz_s1', 0)) if indicators.get('rz_s1') is not None else None
        rz_s2 = float(indicators.get('rz_s2', 0)) if indicators.get('rz_s2') is not None else None
        rz_s3 = float(indicators.get('rz_s3', 0)) if indicators.get('rz_s3') is not None else None
        catcher = float(indicators.get('catcher', 0)) if indicators.get('catcher') is not None else None
        tracer = float(indicators.get('tracer', 0)) if indicators.get('tracer') is not None else None
        neo_lead = float(indicators.get('neo_lead', 0)) if indicators.get('neo_lead') is not None else None
        neo_lag = float(indicators.get('neo_lag', 0)) if indicators.get('neo_lag') is not None else None
        tp1 = float(indicators.get('tp1', 0)) if indicators.get('tp1') is not None else None
        sl1 = float(indicators.get('sl1', 0)) if indicators.get('sl1') is not None else None
        tp2 = float(indicators.get('tp2', 0)) if indicators.get('tp2') is not None else None
        sl2 = float(indicators.get('sl2', 0)) if indicators.get('sl2') is not None else None

        # Determine position type and exit signal based on alert
        position_type = None
        is_exit = "Exit" in alert
        if "Bullish" in alert:
            position_type = "LONG" if not is_exit else None
        elif "Bearish" in alert:
            position_type = "SHORT" if not is_exit else None

        logging.info(f"Received Signal - Alert: {alert}, Symbol: {symbol}, Position: {position_type}, "
                     f"Price: {close_price}, TP1: {tp1}, SL1: {sl1}, Is Exit: {is_exit}")

        client = get_binance_client()
        if not client:
            raise Exception("Failed to initialize Binance client")

        message = 'Webhook received successfully, no trade executed'
        macd_result = None

        if position_type:  # Confirmation signal (Bullish or Bearish without Exit)
            macd_result = calculate_macd(client, symbol, tf)
            if not macd_result['histogram']:
                raise Exception("MACD calculation failed")

            latest_histogram = macd_result['histogram']
            prev_histogram = macd_result['prev_histogram']
            histogram_ratio = abs(latest_histogram) / abs(prev_histogram) if prev_histogram != 0 else float('inf')
            swing = (latest_histogram * prev_histogram < 0) if prev_histogram != 0 else False

            logging.info(f"MACD Results: {macd_result}")
            logging.info(f"Histogram Ratio: {histogram_ratio}, Swing: {swing}")

            if (position_type == "LONG" and latest_histogram > 0 and
                    (swing or (prev_histogram != 0 and histogram_ratio <= 0.66))):
                logging.info(f"TRIGGER LONG SETUP - Ratio: {histogram_ratio}, Swing: {swing}")
                message = "LONG SETUP - Trade Triggered"
                invoke_trading_lambda(symbol, "open_long_sl_tp_without_investment",
                                      position_type, tp1, sl1, close_price)
            elif (position_type == "SHORT" and latest_histogram < 0 and
                  (swing or (prev_histogram != 0 and histogram_ratio >= 0.66))):
                logging.info(f"TRIGGER SHORT SETUP - Ratio: {histogram_ratio}, Swing: {swing}")
                message = "SHORT SETUP - Trade Triggered"
                invoke_trading_lambda(symbol, "open_short_sl_tp_without_investment",
                                      position_type, tp1, sl1, close_price)
            else:
                logging.info(f"NON-CONFIRM SIGNAL SETUP - Ratio: {histogram_ratio}, Swing: {swing}")
                message = "NON-CONFIRM SIGNAL SETUP - Take Profit Partially"
                invoke_trading_lambda(symbol, "take_profit_partially")

        if is_exit:  # Exit signal (Bullish Exit or Bearish Exit)
            logging.info("TRIGGER EXIT SETUP")
            message = "EXIT SETUP - Partial Take Profit Triggered"
            invoke_trading_lambda(symbol, "take_profit_partially")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "macd": macd_result,
                "interval": tf,
                "signal": {
                    "alert": alert,
                    "symbol": symbol,
                    "position_type": position_type,
                    "close_price": close_price,
                    "take_profit": tp1,
                    "stop_loss": sl1
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
    json_input = {
        "alert": "Bullish Confirmation Signal",
        "ticker": "DOGEUSDT",
        "exchange": "BINANCE",
        "sector": "na",
        "market": "Crypto",
        "tf": "1",
        "bartime": "1741741081000",
        "year": "2025",
        "month": "3",
        "day": "12",
        "ohlcv": {
            "open": "0.16317",
            "high": "0.16319",
            "low": "0.16299",
            "close": "0.16299",
            "volume": "196461"
        },
        "indicators": {
            "smart_trail": "0.1644178133377382",
            "rz_r3": "0.1669824463710614",
            "rz_r2": "0.166080965595846",
            "rz_r1": "0.1651794848206306",
            "rz_s1": "0.1624179780031806",
            "rz_s2": "0.1615164972279652",
            "rz_s3": "0.1606150164527498",
            "catcher": "0.16346498717",
            "tracer": "0.163700210962177",
            "neo_lead": "0.1638974899860026",
            "neo_lag": "0.1620825100139974",
            "tp1": "0.1620825100139974",
            "sl1": "0.1638974899860026",
            "tp2": "0.161184029238782",
            "sl2": "0.164798970761218"
        }
    }
    event = {"body": json.dumps(json_input)}
    response = lambda_handler(event, None)
    print("Response:")
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()