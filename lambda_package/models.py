class SignalData:
    """Class to store parsed signal data as attributes."""

    def __init__(self, data: dict):
        self.alert = data.get('alert', '')
        self.symbol = data.get('ticker', '')
        self.exchange = data.get('exchange', '')
        self.sector = data.get('sector', 'na')
        self.market = data.get('market', 'Crypto')
        self.interval_raw = data.get('interval', '1')
        self.tf = data.get('tf', '')
        self.bartime = data.get('bartime', '')
        self.year = data.get('year', '')
        self.month = data.get('month', '')
        self.day = data.get('day', '')

        ohlcv = data.get('ohlcv', {})
        self.open_price = float(ohlcv.get('open', 0)) if ohlcv.get('open') is not None else 0.0
        self.high_price = float(ohlcv.get('high', 0)) if ohlcv.get('high') is not None else 0.0
        self.low_price = float(ohlcv.get('low', 0)) if ohlcv.get('low') is not None else 0.0
        self.close_price = float(ohlcv.get('close', 0)) if ohlcv.get('close') is not None else 0.0
        self.volume = float(ohlcv.get('volume', 0)) if ohlcv.get('volume') is not None else 0.0

        indicators = data.get('indicators', {})
        self.smart_trail = float(indicators.get('smart_trail', 0)) if indicators.get(
            'smart_trail') is not None else None
        self.rz_r3 = float(indicators.get('rz_r3', 0)) if indicators.get('rz_r3') is not None else None
        self.rz_r2 = float(indicators.get('rz_r2', 0)) if indicators.get('rz_r2') is not None else None
        self.rz_r1 = float(indicators.get('rz_r1', 0)) if indicators.get('rz_r1') is not None else None
        self.rz_s1 = float(indicators.get('rz_s1', 0)) if indicators.get('rz_s1') is not None else None
        self.rz_s2 = float(indicators.get('rz_s2', 0)) if indicators.get('rz_s2') is not None else None
        self.rz_s3 = float(indicators.get('rz_s3', 0)) if indicators.get('rz_s3') is not None else None
        self.trend_catcher = float(indicators.get('catcher', 0)) if indicators.get('catcher') is not None else None
        self.trend_tracer = float(indicators.get('tracer', 0)) if indicators.get('tracer') is not None else None
        self.neo_lead = float(indicators.get('neo_lead', 0)) if indicators.get('neo_lead') is not None else None
        self.neo_lag = float(indicators.get('neo_lag', 0)) if indicators.get('neo_lag') is not None else None
        self.tp1 = float(indicators.get('tp1', 0)) if indicators.get('tp1') is not None else None
        self.sl1 = float(indicators.get('sl1', 0)) if indicators.get('sl1') is not None else None
        self.tp2 = float(indicators.get('tp2', 0)) if indicators.get('tp2') is not None else None
        self.sl2 = float(indicators.get('sl2', 0)) if indicators.get('sl2') is not None else None


class Position:
    def __init__(
            self,
            symbol: str,
            positionSide: str,
            positionAmt: str,
            entryPrice: str,
            breakEvenPrice: str,
            markPrice: str,
            unRealizedProfit: str,
            liquidationPrice: str,
            isolatedMargin: str,
            notional: str,
            marginAsset: str,
            isolatedWallet: str,
            initialMargin: str,
            maintMargin: str,
            positionInitialMargin: str,
            openOrderInitialMargin: str,
            adl: int,
            bidNotional: str,
            askNotional: str,
            updateTime: int,
            position_type: str  # Still keeping this as it’s derived
    ):
        self.symbol = symbol
        self.positionSide = positionSide
        self.positionAmt = positionAmt
        self.entryPrice = entryPrice
        self.breakEvenPrice = breakEvenPrice
        self.markPrice = markPrice
        self.unRealizedProfit = unRealizedProfit
        self.liquidationPrice = liquidationPrice
        self.isolatedMargin = isolatedMargin
        self.notional = notional
        self.marginAsset = marginAsset
        self.isolatedWallet = isolatedWallet
        self.initialMargin = initialMargin
        self.maintMargin = maintMargin
        self.positionInitialMargin = positionInitialMargin
        self.openOrderInitialMargin = openOrderInitialMargin
        self.adl = adl
        self.bidNotional = bidNotional
        self.askNotional = askNotional
        self.updateTime = updateTime
        self.position_type = position_type

    def __repr__(self):
        return (f"<Position: {self.position_type}, Symbol: {self.symbol}, "
                f"Amount: {self.positionAmt}, Entry Price: {self.entryPrice}>")
