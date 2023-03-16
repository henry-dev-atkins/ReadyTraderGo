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
import time 
import numpy as np
import asyncio.base_events
from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 5
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

PLACE_ORDER_WAIT_TIME = 0.2


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
        self.HEDGE_bids = set()
        self.HEDGE_asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = self.HedgePosition = 0
        self.data = [] # bids, bid vol, ask, ask vol
        self.last_best_bid = 0
        self.last_best_ask = 0
        self.loop = asyncio.new_event_loop()
        self.epoch = time.asctime(time.gmtime(0))
        self.best_ask = self.best_bid = 0,0
        self.spread = 0
        self.mid = 0
        self.waitingOrders = 0
        self.messeges_in_s = 0
        self.lastTime = self.event_loop.time()
        self.current_etf_ask_prices = self.current_etf_ask_volumes = self.current_etf_bid_prices = self.current_etf_bid_volumes = []
        self.current_fut_ask_prices = self.current_fut_ask_volumes = self.current_fut_bid_prices = self.current_fut_bid_volumes = []
    
    def calcOptimalPosition(self):
        size = abs(self.position)
        if size < 10: return 5
        else: return int(0.5 * size)
        
    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,price, volume)
        if client_order_id in self.HEDGE_bids:
            self.HedgePosition += volume
        elif client_order_id in self.HEDGE_asks:
            self.HedgePosition -= volume
            
    def checkOneSecond(self):
        now = self.event_loop.time()
        if now - self.lastTime >= 1:
            self.messeges_in_s = 0
        
    def placeBuy(self, price:int):
        #print("BUY: ", self.waitingOrders, self.messeges_in_s, abs(self.position))
        if self.waitingOrders >= 9 or self.messeges_in_s >= 25 or abs(self.position) >=99:return
        self.bid_id = next(self.order_ids)
        self.bid_price = int(price)
        size = self.calcOptimalPosition()
        #print("87", self.bid_id, Side.BUY, self.bid_price, size, Lifespan.GOOD_FOR_DAY)
        self.send_insert_order(self.bid_id, Side.BUY, self.bid_price, size, Lifespan.GOOD_FOR_DAY)
        self.messeges_in_s += 1
        self.bids.add(self.bid_id)

    def placeSell(self, price:int):
        #print("SELL:", self.waitingOrders >= 9 or self.messeges_in_s >= 25 or abs(self.position) >= 99)
        if self.waitingOrders >= 9 or self.messeges_in_s >= 25 or abs(self.position) >= 99:return
        self.ask_id = next(self.order_ids)
        self.ask_price = int(price)
        size = self.calcOptimalPosition()
        #print("96", self.ask_id, Side.SELL, self.ask_price, size, Lifespan.GOOD_FOR_DAY)
        self.send_insert_order(self.ask_id, Side.SELL, self.ask_price, size, Lifespan.GOOD_FOR_DAY)
        self.messeges_in_s += 1
        self.asks.add(self.ask_id)

    def checkEqualOppPosition(self):
        if self.position == - self.HedgePosition: return
        else:
            self.logger.info("Unbalanced Trading: pos, fut: %d %d",self.position, self.HedgePosition)
        
    def scheduled_placing_in_spread(self):#print(self.mid, self.spread, self.mid - self.spread, self.mid + self.spread, TICK_SIZE_IN_CENTS, np.arange(self.mid - self.spread, self.mid + self.spread, TICK_SIZE_IN_CENTS))
        if self.mid == 0 or self.spread <= 50 or self.spread == 5000:return
        
        bid_to_mid_range = np.arange(max(self.best_bid, self.mid-4*TICK_SIZE_IN_CENTS), self.mid, TICK_SIZE_IN_CENTS)
        if self.position < 0:
            for i in bid_to_mid_range:
                if len(self.bids) < 5 and i < self.mid:self.placeBuy(i-TICK_SIZE_IN_CENTS)
        
        mid_to_ask_range = np.arange(self.mid, min(self.best_ask, self.mid+4*TICK_SIZE_IN_CENTS), TICK_SIZE_IN_CENTS)
        if self.position > 0:
            for i in mid_to_ask_range:
                if len(self.asks) < 5 and i > self.mid:self.placeSell(i+TICK_SIZE_IN_CENTS)
        
        if self.position > -10 and self.position < 10:
            for i in bid_to_mid_range:
                #print("len(self.bids): ", len(self.bids))
                if len(self.bids) < 5 and i < self.mid:self.placeBuy(i-TICK_SIZE_IN_CENTS)
            for i in mid_to_ask_range:
                if len(self.asks) < 5 and i > self.mid:self.placeSell(i+TICK_SIZE_IN_CENTS)

    def scheduled_cancelling(self):#print("Cancelling: ", len(self.asks), len(self.bids))
        for i in self.asks:
            self.send_cancel_order(i)
            self.messeges_in_s += 1
        for i in self.bids:
            self.send_cancel_order(i)
            self.messeges_in_s += 1
        
    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,sequence_number)
        self.checkOneSecond()
        self.checkEqualOppPosition()

        if instrument == Instrument.ETF:
            #print("on_order_book_update_m", instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)
            self.spread = abs(min(ask_prices[0] - bid_prices[0], 5000))
            self.best_ask = ask_prices[0]
            self.best_bid = bid_prices[0]
            self.current_etf_ask_prices = list(ask_prices)
            self.current_etf_ask_volumes = list(ask_volumes)
            self.current_etf_bid_prices = list(bid_prices)
            self.current_etf_bid_volumes = list(bid_volumes)

        if instrument == Instrument.FUTURE:
            self.scheduled_cancelling()
            self.mid = int(np.round((ask_prices[0] - bid_prices[0])/2 + bid_prices[0], -2))
            self.loop.call_at(time.time() + PLACE_ORDER_WAIT_TIME, self.scheduled_placing_in_spread())
            self.current_fut_ask_prices = list(ask_prices)
            self.current_fut_ask_volumes = list(ask_volumes)
            self.current_fut_bid_prices = list(bid_prices)
            self.current_fut_bid_volumes = list(bid_volumes)


    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,price, volume)
        self.checkOneSecond()
        if client_order_id in self.bids:
            self.position += volume
            id = next(self.order_ids)
            self.send_hedge_order(id, Side.ASK, MIN_BID_NEAREST_TICK, volume)
            self.messeges_in_s += 1
            self.HEDGE_asks.add(id)
        elif client_order_id in self.asks:
            self.position -= volume
            id = next(self.order_ids)
            self.send_hedge_order(id, Side.BID, MAX_ASK_NEAREST_TICK, volume)
            self.messeges_in_s += 1
            self.HEDGE_bids.add(id)

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,fees: int) -> None:
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",client_order_id, fill_volume, remaining_volume, fees)
        self.checkOneSecond()
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,sequence_number)
        self.checkOneSecond()
        if instrument == Instrument.ETF:     
            pass
            """
            self.spread = abs(min(ask_prices[0] - bid_prices[0], 5000))
            self.best_ask = ask_prices[0]
            self.best_bid = bid_prices[0]
            self.current_etf_ask_prices = [val for i, val in enumerate(self.current_etf_ask_prices) if val != ask_prices[i] else ask_prices[i]] #list(ask_prices) 
                    self.current_etf_ask_volumes = list(ask_volumes)
                    self.current_etf_bid_prices = list(bid_prices)
                    self.current_etf_bid_volumes = list(bid_volumes)
            #print("on_trade_ticks_message", instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)
            """

        if instrument == Instrument.FUTURE:
            pass
            """self.scheduled_cancelling()
            self.mid = int(np.round((ask_prices[0] - bid_prices[0])/2 + bid_prices[0], -2))
            self.loop.call_at(time.time() + PLACE_ORDER_WAIT_TIME, self.scheduled_placing_in_spread())
            self.current_fut_ask_prices = ask_prices
            self.current_fut_ask_volumes = ask_volumes
            self.current_fut_bid_prices = bid_prices
            self.current_fut_bid_volumes = bid_volumes"""