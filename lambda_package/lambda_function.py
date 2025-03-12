import json
import logging
import os

import boto3
from binance.um_futures import UMFutures
from order_processor import create_order_with_sl_tp, close_position, clear_all_symbol_orders, take_profit_partially
from price_calculation_processor import get_current_balance_in_usdt, calculate_sl_tp_prices, \
    calculate_params_with_sl_tp_wihtout_invest_percentage

# Configure logging with file and function prefix
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


def lambda_handler(event, context):
    logging.info("START: lambda_handler")
    logging.info(f"Event received: {json.dumps(event)}")
    try:
        body = json.loads(event["body"]) if "body" in event else event
        client = get_binance_client()
        symbol = body.get("symbol")
        action = body.get("action")

        if not symbol or not action:
            raise ValueError("Missing symbol or action")

        if action == "get_balance":
            result = get_current_balance_in_usdt(client)
        elif action in ["open_long_sl_tp", "open_short_sl_tp"]:
            investment_percentage = float(body["investment_percentage"])
            max_loss_percentage = float(body["max_loss_percentage"])
            leverage = float(body["leverage"])
            risk_reward_ratio = body["risk_reward_ratio"]
            position_type = "LONG" if action == "open_long_sl_tp" else "SHORT"

            # Step 1: Calculate SL, TP, and investment
            calc_result = calculate_sl_tp_prices(client, symbol, position_type,
                                                 investment_percentage, max_loss_percentage,
                                                 risk_reward_ratio, leverage)
            if "status" in calc_result and calc_result["status"] == "error":
                logging.error(f"Calculation failed: {calc_result['message']}")
                return calc_result

            # Step 2: Create order with calculated values
            result = create_order_with_sl_tp(
                client, symbol, position_type,
                stop_loss_price=calc_result["stop_loss_price"],
                take_profit_price=calc_result["take_profit_price"],
                quantity=calc_result["quantity"],
                investment_amount=calc_result["investment_amount"],
                market_price=calc_result["market_price"]
            )
        elif action in ["open_long_sl_tp_without_investment", "open_short_sl_tp_without_investment"]:
            stop_loss_price = float(body["stop_loss_price"])
            take_profit_price = float(body["take_profit_price"])
            investment_percentage = float(body["investment_percentage"])
            leverage = float(body["leverage"])
            position_type = "LONG" if action == "open_long_sl_tp" else "SHORT"

            # Step 1: Calculate SL, TP, and investment
            calc_result = calculate_params_with_sl_tp_wihtout_invest_percentage(client, symbol, position_type,
                                                                                stop_loss_price, take_profit_price,
                                                                                investment_percentage, leverage)
            if "status" in calc_result and calc_result["status"] == "error":
                logging.error(f"Calculation failed: {calc_result['message']}")
                return calc_result

            # Step 2: Create order with calculated values
            result = create_order_with_sl_tp(
                client, symbol, position_type,
                stop_loss_price=calc_result["stop_loss_price"],
                take_profit_price=calc_result["take_profit_price"],
                quantity=calc_result["quantity"],
                investment_amount=calc_result["investment_amount"],
                market_price=calc_result["market_price"]
            )
        elif action == "close_all_symbol_orders":
            result = close_position(client, symbol, "LONG")
            logging.info(f"Close Long: {result}")
            result = close_position(client, symbol, "SHORT")
            logging.info(f"Close Short: {result}")
        elif action == "take_profit_partially":
            symbol = body.get("symbol")
            result = take_profit_partially(client, symbol)
        elif action == "clear_orders":
            result = clear_all_symbol_orders(client, symbol)
        else:
            result = {"status": "error", "message": "Invalid action"}

        logging.info(f"Lambda result: {result}")
        return result
    except Exception as e:
        logging.error(f"Unhandled Exception: {str(e)}")
        return {"status": "error", "message": str(e)}


def test_lambda(action, symbol=None, take_profit_price=None,
                stop_loss_price=None, investment_percentage=None,
                position_type=None, max_loss_percentage=None,
                risk_reward_ratio=None, leverage=10):
    logging.info("START: test_lambda")
    event = {
        "body": json.dumps({
            "action": action,
            "symbol": symbol,
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
            "investment_percentage": investment_percentage,
            "position_type": position_type,
            "max_loss_percentage": max_loss_percentage,
            "risk_reward_ratio": risk_reward_ratio,
            "leverage": leverage
        })
    }
    logging.info(f"Test event: {event}")
    context = {}
    response = lambda_handler(event, context)
    logging.info(f"Test response: {json.dumps(response, indent=4)}")
    print(json.dumps(response, indent=4))


if __name__ == "__main__":
    logging.info("Starting test execution")
    # test_lambda("open_short_sl_tp_without_investment", "DOGEUSDT", stop_loss_price=0.16941, take_profit_price=0.16207,
    #               investment_percentage=3.0, leverage=10)
    test_lambda("close_all_symbol_orders", "DOGEUSDT")
    # test_lambda("clear_orders", "DOGEUSDT")
    # test_lambda("take_profit_partially", "DOGEUSDT")
    logging.info("Test execution completed")
