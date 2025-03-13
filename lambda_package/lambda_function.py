import json
import logging
import os
from typing import Dict

from algorithm import calculate_macd
from binance_trade_wrapper import get_binance_client
from order_processor import handle_order_logic
from config import INVESTMENT_PERCENTAGE, LEVERAGE, MACD_DELTA_RATIO  # Import from config

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)

def lambda_handler(event: Dict, context: object) -> Dict:
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
        interval_raw = body.get('interval', '1')

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
            macd_result = calculate_macd(client=client, symbol=symbol, interval_raw=interval_raw, limit=500)
            if not macd_result['histogram']:
                raise Exception("MACD calculation failed")

            latest_histogram = macd_result['histogram']
            prev_histogram = macd_result['prev_histogram']
            swing = (latest_histogram * prev_histogram < 0) if prev_histogram != 0 else False
            ratio_condition_met = False

            if not swing and prev_histogram != 0:
                if position_type == "LONG":
                    if latest_histogram < 0 and prev_histogram < 0:
                        ratio_condition_met = abs(latest_histogram) < MACD_DELTA_RATIO * abs(prev_histogram)
                    elif latest_histogram > 0 and prev_histogram > 0:
                        ratio_condition_met = abs(prev_histogram) < MACD_DELTA_RATIO * abs(latest_histogram)
                elif position_type == "SHORT":
                    if latest_histogram < 0 and prev_histogram < 0:
                        ratio_condition_met = abs(latest_histogram) > MACD_DELTA_RATIO * abs(prev_histogram)
                    elif latest_histogram > 0 and prev_histogram > 0:
                        ratio_condition_met = abs(prev_histogram) > MACD_DELTA_RATIO * abs(latest_histogram)

            logging.info(f"MACD Results: {macd_result}")
            logging.info(f"Swing: {swing}, Ratio Condition Met: {ratio_condition_met}")

            if position_type == "LONG" and (swing or ratio_condition_met):
                logging.info(f"TRIGGER LONG SETUP - Swing: {swing}, Ratio: {ratio_condition_met}")
                message = "LONG SETUP - Trade Triggered"
                order_event = {
                    "symbol": symbol,
                    "action": "open_long_sl_tp_without_investment",
                    "stop_loss_price": sl1,
                    "take_profit_price": tp2,
                    "investment_percentage": INVESTMENT_PERCENTAGE,
                    "leverage": LEVERAGE
                }
                result = handle_order_logic({"body": json.dumps(order_event)})
                if "status" in result and result["status"] == "error":
                    message = f"LONG SETUP - Failed: {result['message']}"
            elif position_type == "SHORT" and (swing or ratio_condition_met):
                logging.info(f"TRIGGER SHORT SETUP - Swing: {swing}, Ratio: {ratio_condition_met}")
                message = "SHORT SETUP - Trade Triggered"
                order_event = {
                    "symbol": symbol,
                    "action": "open_short_sl_tp_without_investment",
                    "stop_loss_price": sl1,
                    "take_profit_price": tp2,
                    "investment_percentage": INVESTMENT_PERCENTAGE,
                    "leverage": LEVERAGE
                }
                result = handle_order_logic({"body": json.dumps(order_event)})
                if "status" in result and result["status"] == "error":
                    message = f"SHORT SETUP - Failed: {result['message']}"
            else:
                logging.info(f"NON-CONFIRM SIGNAL SETUP - Swing: {swing}, Ratio: {ratio_condition_met}")
                message = "NON-CONFIRM SIGNAL SETUP - Take Profit Partially"
                order_event = {
                    "symbol": symbol,
                    "action": "take_profit_partially"
                }
                result = handle_order_logic({"body": json.dumps(order_event)})
                if "status" in result and result["status"] == "error":
                    message = f"PARTIAL TAKE PROFIT - Failed: {result['message']}"

        if is_exit:  # Exit signal (Bullish Exit or Bearish Exit)
            logging.info("TRIGGER EXIT SETUP")
            message = "EXIT SETUP - Partial Take Profit Triggered"
            order_event = {
                "symbol": symbol,
                "action": "take_profit_partially"
            }
            result = handle_order_logic({"body": json.dumps(order_event)})
            if "status" in result and result["status"] == "error":
                message = f"EXIT SETUP - Failed: {result['message']}"

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
                    "take_profit": tp2,
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
        "interval": "1",
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
