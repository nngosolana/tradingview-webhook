# tradingview-webhook
now, I want to clean all my logic of signal processor. Now I only care with the Bearish/Bullish Confirmation, Bearish/Bullish Exit, TP1/TP2 Reach, SL1/SL2 Reach.
- Now the rule we trigger position is different, we only trigger the position if the TP1 Reach.
  When the TP1 Reach, we have two conditions:
+ If the Position is exists with same type, ignore this signal
+ If the Position is exists with different type, close the current position and trigger new position with entry price is the TP1, SL is SL2 Value, and the TP is base on R:R ( ex: with Long Position, 1:6 mean Take Profit Price = Current Price + 6 x ( Current Price - Stop Loss Price ), with Short Position, 1:6 mean Take Profit Price = Current Price - 6 x ( Stop Loss Price - Current Price)
+ If the Position is not existys, trigger new position with same logic with "If the Position is exists with different type"

When the TP2 Reach, we will slightly move the SL to SL1 ( we inital it as SL2 as previous ) and continue move TP to new value with R:Reach

When the SL1 or SL2 Reach, ignore all conditions, we handle it by ourself.
- When the Bearish or Bullish Exit, we do the logic as: Take Profit Partially with 40% of total investment, we recalculate the new SL Price:
+ with Long, If the current SL Price is less than the ( current price + entry price ) / 2, set SL Price = ( current price + entry price ) / 2, if the SL is greater than ( current price + entry price ) / 2, keep current SL. Finally, if the SL < entry price, make SL  = entry price
+ with Short, If the current SL Price is greater than the ( current price + entry price ) / 2, set SL Price = ( current price + entry price ) / 2, if the SL is less than ( current price + entry price ) / 2, keep current SL. Finally, if the SL > entry price, make SL  = entry price

When the Bearish/Bullish Confirmation, we slight do one thing, if Bearish Confirmation ,
+ if any Long position exists-> close Long and do nothing,
+ if any Short Positions Exists -> do no thing.
  if Bullish Confirmation ,
+ if any Short position exists-> close Short and do nothing,
+ if any Long Positions Exists -> do no thing.

We still keep the signal score, but do it different, base on the score we will adjust the total investment base on percentage, if score is 65/100, we invest 65% on maximum investment.
We only send the message to discord once per singal, collect all information and send once, it's better to send with table, build the library to send the data in table with multiple color to save space.
 