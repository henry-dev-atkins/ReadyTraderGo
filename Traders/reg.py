"""
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

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


class AutoTrader(BaseAutoTrader):

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0
        self.data = [] # bids, bid vol, ask, ask vol
        self.last_best_bid = 0
        self.last_best_ask = 0
        self.ETF_bids = None
        self.ETF_asks = None
        self.ETF_bid_vol = None
        self.ETF_ask_vol = None
        self.asksAdded = []
        self.bidsAdded = []
        self.activeOrders = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        if instrument == Instrument.FUTURE:
            self.Future_best_ask = ask_prices[0]
            self.Future_best_bid = bid_prices[0]

        if instrument == Instrument.ETF:
            self.ETF_bids = bid_prices
            self.ETF_asks = ask_prices
            self.ETF_bid_vol = bid_volumes
            self.ETF_ask_vol = ask_volumes
            self.etf_best_ask = ask_prices[0]
            self.etf_best_bid = bid_prices[0]

            for i in self.asksAdded:
                if i in self.asks:
                    self.send_cancel_order(i)
                    self.asks_id = 0
            for i in self.bidsAdded:
                if i in self.bids:
                    self.send_cancel_order(i)
                    self.bids_id = 0

        if self.ETF_asks and instrument == Instrument.FUTURE:
            self.asksAdded = []
            self.bidsAdded = []
            spread_tick_count = 0
            possible_order_count = 8 + spread_tick_count
            if self.position >= 0:
                for i, price in enumerate(bid_prices):
                    if i < 6 and self.ETF_bid_vol[i] == 0 and self.activeOrders < 19:
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.bid_id, Side.BUY, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.bid_id)
                        self.bidsAdded.append(self.bid_id)
                        self.activeOrders += 1
            if self.position <= 0:
                for i, price in enumerate(ask_prices):
                    if i < 6 and self.ETF_ask_vol[i] == 0 and self.activeOrders < 19:
                        self.ask_id = next(self.order_ids)
                        self.send_insert_order(self.ask_id, Side.SELL, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.ask_id)
                        self.asksAdded.append(self.ask_id)
                        self.activeOrders += 1

                    
    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK + TICK_SIZE_IN_CENTS , volume)

        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK - TICK_SIZE_IN_CENTS, volume)
        

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
                self.activeOrders -= 1
            elif client_order_id == self.ask_id:
                self.ask_id = 0
                self.activeOrders -= 1

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
"""

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

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side
import numpy as np

LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


class AutoTrader(BaseAutoTrader):
    

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0
        self.data = [] # bids, bid vol, ask, ask vol
        self.last_best_bid = 0
        self.last_best_ask = 0
        self.ETF_bids = None
        self.ETF_asks = None
        self.ETF_bid_vol = None
        self.ETF_ask_vol = None
        self.asksAdded = []
        self.bidsAdded = []
        self.activeOrders = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        if instrument == Instrument.FUTURE:
            self.Future_best_ask = ask_prices[0]
            self.Future_best_bid = bid_prices[0]

        if instrument == Instrument.ETF:
            self.ETF_bids = bid_prices
            self.ETF_asks = ask_prices
            self.ETF_bid_vol = bid_volumes
            self.ETF_ask_vol = ask_volumes
            self.etf_best_ask = ask_prices[0]
            self.etf_best_bid = bid_prices[0]

            for i in self.asksAdded:
                if i in self.asks:
                    self.send_cancel_order(i)
                    self.asks_id = 0
                    
            for i in self.bidsAdded:
                if i in self.bids:
                    self.send_cancel_order(i)
                    self.bids_id = 0

        if self.ETF_asks and instrument == Instrument.FUTURE and self.etf_best_ask != 0:
            self.asksAdded = []
            self.bidsAdded = []
            spread_tick_count = 0
            mid = np.round(self.etf_best_bid + ((self.etf_best_ask - self.etf_best_bid) //2), TICK_SIZE_IN_CENTS) if self.etf_best_ask != self.etf_best_bid + TICK_SIZE_IN_CENTS else 0
            #print("mid: ", mid, self.etf_best_ask, self.etf_best_bid)
            possible_order_count = 8 + spread_tick_count
            if self.position >= 0:
                for i, price in enumerate(np.arange(bid_prices[-1] + TICK_SIZE_IN_CENTS, mid + TICK_SIZE_IN_CENTS, TICK_SIZE_IN_CENTS)):
                    if i < 6 and self.activeOrders < 9 and price not in bid_prices:
                        self.bid_id = next(self.order_ids)
                        #print(self.bid_id, Side.BUY, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.send_insert_order(self.bid_id, Side.BUY, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.bid_id)
                        self.bidsAdded.append(self.bid_id)
                        self.activeOrders += 1
            if self.position <= 0:
                for i, price in enumerate(np.arange(mid - TICK_SIZE_IN_CENTS, ask_prices[-1], TICK_SIZE_IN_CENTS)):
                    if i < 6 and self.activeOrders < 9 and price not in ask_prices:
                        self.ask_id = next(self.order_ids)
                        #print(self.ask_id, Side.SELL, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.send_insert_order(self.ask_id, Side.SELL, price, 5, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.ask_id)
                        self.asksAdded.append(self.ask_id)
                        self.activeOrders += 1

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)
        

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
                self.activeOrders -= 1
            elif client_order_id == self.ask_id:
                self.ask_id = 0
                self.activeOrders -= 1

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
        
