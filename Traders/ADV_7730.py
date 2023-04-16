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


LOT_SIZE = 20
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
        self.future_ask=[0]
        self.future_bid=[0]
        self.ETF_ask=[0]
        self.ETF_bid=[0]
        self.fee=0
        self.hedge=0
        self.hedge_asks=set()
        self.hedge_bids=set()
        self.hedge_vol_ask=0
        self.hedge_vol_bid=0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:

        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)
        # if client_order_id in self.hedge_asks:
        #     self.hedge+=volume
        #     vol = self.hedge_vol_ask-volume
        #     self.send_hedge_order(next(self.order_ids), Side.BID, price, volume)
        # elif client_order_id in self.hedge_bids:
        #     self.hedge-=volume   
        #     vol = self.hedge_vol_bid-volume 
        #     self.send_hedge_order(next(self.order_ids),Side.BID, MIN_BID_NEAREST_TICK,vol)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,sequence_number)
        
        new_ask_price=ask_prices[0]
        new_bid_price=bid_prices[0]
        if instrument == Instrument.FUTURE:
            self.future_ask=ask_prices
            self.future_bid=bid_prices
            self.fee=0.0002*self.future_ask[0]
            return
            
        if instrument == Instrument.ETF:
            self.ETF_bid = bid_prices
            self.ETF_ask = ask_prices
            ETFspread = ask_prices[0]-bid_prices[0]
            if self.ETF_bid[0] > self.future_ask[0]+ self.fee + ETFspread:
                 new_ask_price = bid_prices[0] if bid_prices[0] != 0 else 0
                 
            if self.ETF_ask[0] < self.future_bid[0]-self.fee - ETFspread:
                 new_bid_price = ask_prices[0] if bid_prices[0] != 0 else 0

        if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
            self.send_cancel_order(self.bid_id)
            self.bid_id = 0
        if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
            self.send_cancel_order(self.ask_id)
            self.ask_id = 0

        if self.bid_id == 0 and new_bid_price != 0 and self.position +LOT_SIZE< POSITION_LIMIT:
            self.bid_id = next(self.order_ids)
            self.bid_price = new_bid_price
            self.logger.info("Position: %d ID: %d",self.position, self.bid_id)
            self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE, Lifespan.F)
            self.bids.add(self.bid_id)
                

        if self.ask_id == 0 and new_ask_price != 0 and self.position -LOT_SIZE > -POSITION_LIMIT:
            self.ask_id = next(self.order_ids)
            self.ask_price = new_ask_price
            self.logger.info("Position: %d \n ID: %d",self.position, self.ask_id)
            self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE, Lifespan.F)
            self.asks.add(self.ask_id)
   

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)



    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,fees: int) -> None:
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,sequence_number)