import os

# Global configuration variables with hardcoded defaults
INVESTMENT_PERCENTAGE = float(os.getenv("INVESTMENT_PERCENTAGE", "3.0"))  # Default 3%
LEVERAGE = int(os.getenv("LEVERAGE", "10"))  # Default 10x
MAX_LOSS_PERCENTAGE = float(os.getenv("MAX_LOSS_PERCENTAGE", "3.0"))  # Default 3%
RISK_REWARD_RATIO = float(os.getenv("RISK_REWARD_RATIO", "2.0"))  # Default 2:1
MACD_DELTA_RATIO = float(os.getenv("MACD_DELTA_RATIO", "0.66"))  # Default 0.66
ENABLE_MACD_CHECK = os.getenv("ENABLE_MACD_CHECK", "True").lower() == "true"
ORDER_MAX_LIMIT_PERCENTAGE = int(os.getenv("ORDER_MAX_LIMIT_PERCENTAGE", "20"))  #  max 20% of total balance per order
