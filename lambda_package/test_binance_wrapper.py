import json
import logging
from binance_trade_wrapper import (
    get_binance_client, fetch_current_price,
    get_rounded_price, get_exchange_info,
    execute_trade_with_tp_sl
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(filename)s:%(funcName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MARGIN = 5
LEVERAGE = 5

def test_order_with_tp_sl(symbol: str = "DOGEUSDC"):
    """Test a single BUY order with Take Profit Limit and Stop Limit for DOGEUSDC."""
    logger.info(f"START: test_order_with_tp_sl - Input: symbol={symbol}")

    # Initialize client
    client = get_binance_client()
    if not client:
        logger.error("Failed to initialize Binance client")
        return

    # Fetch current price for realistic test values
    current_price = fetch_current_price(symbol)
    if current_price == 0.0:
        logger.error("Failed to fetch current price, using fallback value")
        current_price = 0.17  # Fallback for DOGEUSDC

    # Test parameters
    margin = MARGIN  # Margin in USD
    leverage = LEVERAGE  # Leverage (e.g., 5x)
    take_profit_price = current_price * 1.03  # 3% above current price for TP
    stop_loss_price = current_price * 0.97    # 3% below current price for SL

    # Round prices according to symbol precision
    price_precision, _ = get_exchange_info(client, symbol)
    rounded_take_profit_price = get_rounded_price(client, symbol, take_profit_price)
    rounded_stop_loss_price = get_rounded_price(client, symbol, stop_loss_price)

    logger.info(f"Order parameters: current_price={current_price}, "
                f"take_profit_price={rounded_take_profit_price}, stop_loss_price={rounded_stop_loss_price}")

    # Execute a BUY order with Take Profit Limit and Stop Limit
    try:
        order_result, take_profit_result, stop_loss_result = execute_trade_with_tp_sl(
            client=client,
            symbol=symbol,
            side="BUY",  # Change to "SELL" if you want a sell order
            margin=margin,
            leverage=leverage,
            take_profit_price=rounded_take_profit_price,
            stop_loss_price=rounded_stop_loss_price,
            order_type="MARKET"  # Using MARKET for the main order; TP/SL will be limit orders
        )

        if order_result and take_profit_result and stop_loss_result:
            logger.info(f"Success: BUY order with TP/SL placed: Order={json.dumps(order_result, indent=2)}, "
                        f"TP={json.dumps(take_profit_result, indent=2)}, SL={json.dumps(stop_loss_result, indent=2)}")
            print(f"\nSuccess: BUY order with TP/SL placed:\nOrder={json.dumps(order_result, indent=2)}\n"
                  f"TP={json.dumps(take_profit_result, indent=2)}\nSL={json.dumps(stop_loss_result, indent=2)}")
        else:
            logger.warning("Failed: One or more orders returned None")
            print("\nFailed: One or more orders returned None")

    except Exception as e:
        logger.error(f"Error executing BUY order with TP/SL: {str(e)}")
        print(f"\nError executing BUY order with TP/SL: {str(e)}")

    logger.info("END: test_order_with_tp_sl")

def main():
    """Run the test for a single order with TP/SL."""
    logger.info("START: main")
    print("Starting Binance order test for DOGEUSDC with TP/SL...")
    test_order_with_tp_sl("DOGEUSDC")
    logger.info("END: main")
    print("Test completed.")

if __name__ == "__main__":
    main()