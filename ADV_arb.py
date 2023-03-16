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
import numpy as np

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


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
        self.data = [] # bids, bid vol, ask, ask vol
        self.last_best_bid = 0
        self.last_best_ask = 0
        self.Future_best_bid = 0
        self.Future_best_ask = 0
        self.etf_best_bid = 0
        self.etf_best_ask = 0
        self.SentArbTrade = False
        self.lotSize = 20
        self.spread = []
        self.waitingOrders = 0
        self.orderBookCounter = 0
        self.runningPnL = 0
        self.arb_ask = 0
        self.arb_bid = 0

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
        if self.orderBookCounter >= 4:
            #print(self.waitingOrders)
            if self.waitingOrders >= 10:
                for i in self.asks:
                    self.send_cancel_order(i)
                for i in self.bids:
                    self.send_cancel_order(i)
            self.orderBookCounter = -1

        if self.SentArbTrade == True:
            self.arbOrderCount += 1
            if self.arbOrderCount > 10:
                self.send_cancel_order(self.arb_ask)
                self.send_cancel_order(self.arb_bid)
                self.SentArbTrade = False

        if instrument == Instrument.FUTURE:
            self.Future_best_ask = ask_prices[0]
            self.Future_best_bid = bid_prices[0]
        if instrument == Instrument.ETF:
            self.etf_best_ask = ask_prices[0]
            self.etf_best_bid = bid_prices[0]

            if self.Future_best_ask > self.etf_best_bid and self.position <= 0:#Buy etf, short future
                if self.bid_id == 0 and self.etf_best_bid != 0 and self.position < POSITION_LIMIT and self.waitingOrders < 10:
                    self.bid_id = next(self.order_ids)
                    self.bid_price = self.etf_best_bid
                    self.send_insert_order(self.bid_id, Side.BUY, self.etf_best_bid, self.lotSize, Lifespan.GOOD_FOR_DAY)
                    self.bids.add(self.bid_id)
                    self.arb_bid = self.bid_id
                    self.waitingOrders += 1
                    self.SentArbTrade = True
                    self.arbOrderCount = 0

            if self.Future_best_bid < self.etf_best_ask and self.position >= 0:#Buy future, short etf 
                if self.ask_id == 0 and self.etf_best_ask != 0 and self.position > -POSITION_LIMIT and self.waitingOrders < 10:
                    self.ask_id = next(self.order_ids)
                    self.ask_price = self.etf_best_ask
                    self.send_insert_order(self.ask_id, Side.SELL, self.etf_best_ask, self.lotSize, Lifespan.GOOD_FOR_DAY)
                    self.asks.add(self.ask_id)
                    self.arb_ask = self.ask_id
                    self.waitingOrders += 1
                    self.SentArbTrade = True
                    self.arbOrderCount = 0

            #Filling the spread with orders of 1
            self.spread.append(self.etf_best_ask - self.etf_best_bid)
            if instrument == Instrument.ETF and self.spread[-1] >= TICK_SIZE_IN_CENTS * 2 and self.SentArbTrade == False:
                midpoint = np.round(self.spread[-1] // 2 + self.etf_best_bid, TICK_SIZE_IN_CENTS)
                for i in np.arange(self.etf_best_bid, self.etf_best_ask + TICK_SIZE_IN_CENTS, TICK_SIZE_IN_CENTS):
                    if self.waitingOrders >= 10:break
                    if i > midpoint and self.position >= 0:
                        self.ask_id = next(self.order_ids)
                        self.send_insert_order(self.ask_id, Side.SELL, i, 2, Lifespan.GOOD_FOR_DAY)
                        self.asks.add(self.ask_id)
                        self.waitingOrders += 1
                        #print("Sell", i)
                    if i < midpoint and self.position <= 0:
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.bid_id, Side.BUY, i, 2, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.bid_id)
                        self.waitingOrders += 1
                        #print("Buy",i, "\n")
                    #print(self.etf_best_bid, self.etf_best_ask)
        self.orderBookCounter += 1

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.
        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)


        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

        
        if self.SentArbTrade == True:
            if client_order_id in self.bids:
                self.position += volume
                if self.position > 0:#short etf, buy future
                    if self.Future_best_ask < self.etf_best_bid:
                        self.ask_id = next(self.order_ids)
                        self.send_insert_order(self.ask_id, Side.SELL, self.Future_best_ask + TICK_SIZE_IN_CENTS, self.lotSize, Lifespan.GOOD_FOR_DAY)
                        self.asks.add(self.ask_id)
                        self.waitingOrders += 1
                        self.SentArbTrade = False

            elif client_order_id in self.asks:
                self.position -= volume
                if self.position < 0: #short future, buy etf
                    if self.Future_best_bid > self.etf_best_ask:
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.bid_id, Side.BUY, self.Future_best_bid - TICK_SIZE_IN_CENTS, self.lotSize, Lifespan.GOOD_FOR_DAY)
                        self.bids.add(self.bid_id)
                        self.waitingOrders += 1
                        self.SentArbTrade = False

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.
        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.
        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            self.waitingOrders -= 1

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.
        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.
        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)























""""# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
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
import numpy as np

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
        self.Future_best_bid = 0
        self.Future_best_ask = 0
        self.etf_best_bid = 0
        self.etf_best_ask = 0
        self.SentArbTrade = False
        self.lotSize = 20
        self.spread = []
        self.waitingOrders = 0
        self.orderBookCounter = 0
        self.runningPnL = 0
        self.current_arb_ask = 0
        self.current_arb_bid = 0
        self.messeges = 0
        self.position_list = []

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.arb_bids or client_order_id in  self.maker_asks):
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
            self.etf_best_ask = ask_prices[0]
            self.etf_best_bid = bid_prices[0]
            if self.Future_best_ask > self.etf_best_bid and self.position <= 0:#Buy etf, short future
                if self.bid_id == 0 and self.etf_best_bid != 0 and self.position > -POSITION_LIMIT and self.waitingOrders < 10 and self.messeges < 45:
                    self.bid_id = next(self.order_ids)
                    self.bid_price = self.etf_best_bid
                    self.send_insert_order(self.bid_id, Side.BUY, self.etf_best_bid, self.lotSize, Lifespan.GOOD_FOR_DAY)
                    self.messeges+=1
                    self.bids.add(self.bid_id)
                    self.current_arb_bid = self.bid_id
                    self.waitingOrders += 1
                    self.SentArbTrade = True
                    self.arbOrderCount = 0
            if self.Future_best_bid < self.etf_best_ask and self.position >= 0:#Buy future, short etf 
                if self.ask_id == 0 and self.etf_best_ask != 0 and self.position < POSITION_LIMIT and self.waitingOrders < 10 and self.messeges < 45:
                    self.ask_id = next(self.order_ids)
                    self.ask_price = self.etf_best_ask
                    self.send_insert_order(self.ask_id, Side.SELL, self.etf_best_ask, self.lotSize, Lifespan.GOOD_FOR_DAY)
                    self.messeges+=1
                    self.asks.add(self.ask_id)
                    self.current_arb_ask = self.ask_id
                    self.waitingOrders += 1
                    self.SentArbTrade = True
                    self.arbOrderCount = 0
            
            #if self.messeges> 40: print("Error: Messeges, orderBookCounter: ", self.messeges, self.orderBookCounter)
        #print("Error: Messeges, orderBookCounter: ", self.messeges, self.orderBookCounter)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
            self.messeges+=1
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)
            self.messeges+=1

        if self.SentArbTrade == True:
            if client_order_id in self.bids:
                self.position += volume
                if self.position > 0:#short etf, buy future
                    if self.Future_best_ask < self.etf_best_bid:
                        self.ask_id = next(self.order_ids)
                        self.send_insert_order(self.ask_id, Side.SELL, MIN_BID_NEAREST_TICK, self.lotSize, Lifespan.GOOD_FOR_DAY)
                        self.messeges+=1
                        self.asks.add(self.ask_id)
                        self.waitingOrders += 1
                        self.SentArbTrade = False

            elif client_order_id in self.asks:
                self.position -= volume
                if self.position < 0: #short future, buy etf
                    if self.Future_best_bid > self.etf_best_ask:
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.bid_id, Side.BUY, MAX_ASK_NEAREST_TICK, self.lotSize, Lifespan.GOOD_FOR_DAY)
                        self.messeges+=1
                        self.bids.add(self.bid_id)
                        self.waitingOrders += 1
                        self.SentArbTrade = False

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            self.waitingOrders -= 1

            # It could be either a bid or an ask

            self.asks.discard(client_order_id)
            self.bids.discard(client_order_id)
    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
        
"""