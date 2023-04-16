# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import math
import statistics
from typing import List
from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side
import numpy as np
import pandas as pd
import scipy as spy
LOT_SIZE = 5
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
moving_price = []
moving_price_trend = []
df_bid = pd.DataFrame(columns=['Id', 'Price','Lot','Time'])
df_ask = pd.DataFrame(columns=['Id', 'Price','Lot','Time'])
timer = 0 
mid_price = 0
standard_deviation = 0
volatility = 0
T_volatility = 0
q = 0
gamma = 0.0005
T_minus_t = 0
reservation_price = 0
sigma_squared = 0
k = 0
alpha = 0.1
spread = 0
class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.
    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """
    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0
    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.
        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)
    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.
        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)
    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.
        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        global moving_price
        global moving_price_trend
        global timer
        timer += 1
        global mid_price
        global standard_deviation
        global volatility
        global T_volatility
        global q
        global gamma
        global T_minus_t
        global sigma_squared
        global reservation_price
        global k 
        global spread
        global alpha
        if instrument == Instrument.FUTURE:
            max_bid = max(bid_prices)
            min_ask = min(ask_prices)
            max_bid_volume = bid_volumes[0]
            min_ask_volume = ask_volumes[0]
            total_volume = min_ask_volume + max_bid_volume
            
            if total_volume != 0:
 
                futures_price = np.divide((max_bid_volume*max_bid + min_ask_volume*min_ask), total_volume)
                moving_price.append(futures_price)
                # avg_moving_price = 0
                
                #print("Moving Price: ", moving_price)
                #creating boundaries of 2 sd around moving average to define a trend
                # two_standard_deviation = 10*(np.std(moving_price)+0.1)
                # lower_boundaries = avg_moving_price - two_standard_deviation
                # upper_boundaries = avg_moving_price - two_standard_deviation
                mid_price = futures_price 
                standard_deviation = np.var(moving_price)  
                # T_volatility = math.floor(timer/2)
                # volatility = standard_deviation * math.sqrt(T_volatility)  #sigma
                sigma_squared = standard_deviation /20000
                
                gamma = gamma
                q = self.position
                T_minus_t = 1 - (timer/7200) #jgn math.floor nanti jadi 0 timer/7200 nya
                #if len(moving_price)>2:
                #    print("Statistics Variance: ", statistics.variance(moving_price))
                #print("Numpy Variance: ", sigma_squared)
                reservation_price = mid_price - q*gamma*sigma_squared*T_minus_t
                if len(moving_price) > 10:
                    moving_price = moving_price[1:]
                # avg_moving_price = np.divide(sum(moving_price), len(moving_price))
                # bid_ask_price = avg_moving_price 
                bid_ask_price = reservation_price
                #print("Reservation Price: ", reservation_price)
                # print("Average moving price: ", avg_moving_price)
                # new_bid_price = math.floor(bid_ask_price/100)*100
                # new_ask_price = math.ceil(bid_ask_price/100)*100
                
                #print(mid_price, standard_deviation, sigma_squared, T_minus_t)
                #print("\n")
                new_bid_price = math.floor((reservation_price - spread/2)/100)*100
                new_ask_price = math.ceil((reservation_price + spread/2)/100)*100
            
                #print("New bid price: ", new_bid_price)
                #print("New ask price: ", new_ask_price)
                #print("Q: ",q)
                # if there is uptrend, reset the moving average
                # if min_ask < lower_boundaries:
                #     # moving_price = []
                #     # moving_price.append(futures_price)
                #     bid_ask_price = max_bid 
                #     new_bid_price = math.floor(bid_ask_price/100)*100-100
                #     new_ask_price = math.ceil(bid_ask_price/100)*100-100
                # if max_bid > upper_boundaries:
                #     # moving_price = []
                #     # moving_price.append(futures_price)
                #     bid_ask_price = min_ask
                #     new_bid_price = math.floor(bid_ask_price/100)*100+100
                #     new_ask_price = math.ceil(bid_ask_price/100)*100+100
            else:
                new_bid_price = 0
                new_ask_price = 0
            if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0
            if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0
            if self.bid_id == 0 and new_bid_price != 0 and self.position < POSITION_LIMIT-2*LOT_SIZE:
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_price
                self.send_insert_order(self.bid_id, Side.BUY, self.bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)
                df_bid.loc[len(df_bid)] = [self.bid_id,self.bid_price,LOT_SIZE,timer]
            # print("Checking errors: ", self.bid_id, self.bid_price, LOT_SIZE)
            if self.ask_id == 0 and new_ask_price != 0 and self.position > -POSITION_LIMIT+2*LOT_SIZE:
                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_price
                self.send_insert_order(self.ask_id, Side.SELL, self.ask_price,LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)
                df_ask.loc[len(df_ask)] = [self.ask_id,self.ask_price,LOT_SIZE,timer]
            # if self.position >= POSITION_LIMIT-LOT_SIZE:
            #     self.ask_id = next(self.order_ids)
            #     self.ask_price = new_bid_price-1000
            #     self.send_insert_order(self.ask_id, Side.SELL, self.ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
            #     self.asks.add(self.ask_id)
            # print(self.ask_price)
            # if self.position <= -POSITION_LIMIT+LOT_SIZE:
            #     self.bid_id = next(self.order_ids)
            #     self.bid_price = new_ask_price+1000
            #     self.send_insert_order(self.bid_id, Side.BUY, self.bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
            #     self.bids.add(self.bid_id)
            # print(self.bid_price)
        elif instrument == Instrument.ETF:
            max_bid = max(bid_prices)
            min_ask = min(ask_prices)
            max_bid_volume = np.sum(bid_volumes)
            min_ask_volume = np.sum(ask_volumes)
            total_volume = max_bid_volume + min_ask_volume
            if total_volume != 0:
                k = alpha*(total_volume) + (1-alpha)*k
                k = k/5000
                sigma_squared =1
                spread = gamma*sigma_squared*T_minus_t + 2*math.log(1+gamma/k)/gamma
                spread *= 100
                #print("Kappa: ",k)
                #print("Spread: ", spread)
                # new_bid_price = math.floor((reservation_price - spread/2)/100)*100
                # new_ask_price = math.ceil((reservation_price + spread/2)/100)*100
            else:
                new_bid_price = 0
                new_ask_price = 0