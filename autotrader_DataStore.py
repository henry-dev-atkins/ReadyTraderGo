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
import numpy as np
import itertools

from typing import List

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
        self.FutureData = [] # bids, bid vol, ask, ask vol
        self.ETFData = []
        self.F_BidsList = []
        self.E_AsksList = []
        self.last_best_bid = 0
        self.last_best_ask = 0
        #[[INSTRUMENT, ask5-bestAsk, vol, ask4-bestAsk, vol, ask3-bestAsk, vol, ask2-bestAsk, vol, bestAsk, vol, MIDPOINT, Vol=0, bestBid, vol, bid2-bestBid, vol, , bid3-bestBid, vol, , bid4-bestBid, vol, , bid5-bestBid, vol], [...]]
        self.fileStore = np.zeros(shape=(21)) #[[12 Dim like ^], [12Dim like ^], ... [Each 12dim is for a snapshot]]
        self.numberOfFutureOrderBookMesseges = 0
        self.test_data = np.zeros(shape=(21))
        self.training_data = np.zeros(shape=(21))
    
    def CreateDataArray(self, instrument, bid_prices, b_vol, ask_prices, a_vol):
        """
        Create datastore matrix of shape = (Orderbook Timesteps x 21)
        21 dims are: Asks x5 (normalised to best) and vols, Bids x5 (normalised to best) and vols and Instrument.
        """
        scaled_ask = [((price - ask_prices[0]) if i != 0 else ask_prices[0]) if price != 0 else -0.5 for i, price in enumerate(ask_prices)]
        scaled_bid = [((price - bid_prices[0]) if i != 0 else bid_prices[0]) if price != 0 else -0.5 for i, price in enumerate(bid_prices)]
        a_vol = [i for i in reversed(a_vol)]
        scaled_ask.reverse()

        #data = np.array([[scaled_ask], [i for i in a_vol], [i for i in scaled_bid], [i for i in b_vol], [instrument]])
        data = []
        [data.append(i) for i in scaled_ask]
        [data.append(i) for i in a_vol]
        [data.append(i) for i in scaled_bid]
        [data.append(i) for i in b_vol]
        data.append(instrument)

        self.fileStore = np.vstack((self.fileStore, data))
        return data

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
        if instrument == Instrument.FUTURE:self.numberOfFutureOrderBookMesseges += 1
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,sequence_number)
        self.ETFData.append([ask_prices, ask_volumes, bid_prices, bid_volumes]) if instrument == Instrument.ETF else self.FutureData.append([ask_prices, ask_volumes, bid_prices, bid_volumes])

        data = self.CreateDataArray(instrument, bid_prices, bid_volumes, ask_prices, ask_volumes)

        if self.numberOfFutureOrderBookMesseges % 10 == 0:
            for idx, OrderBook in enumerate(self.fileStore[-10:]):
                if OrderBook[-1] == 0: #if OrderBook is for ETF
                    self.test_data = np.vstack((self.test_data, OrderBook))
                    self.training_data = np.vstack((self.training_data, np.array(self.fileStore[-10+idx-10:-10+idx])))
                    #print(self.training_data)
                    #print(self.fileStore[-10+idx-10:-10+idx].shape)
                    with open('test_data.npy', 'wb') as f:
                        np.save(f, self.test_data)
                    with open('training_data.npy', 'wb') as f:
                        np.save(f, self.training_data)

        """if self.fileStore[-1] == 1 and self.numberOfFutureOrderBookMesseges >= 11: #if Future
            print("Data Shape: ", self.fileStore.shape)
            self.training_data.append(self.fileStore[-1])
            train = []
            for val in self.fileStore[-11:-1]:
                train.append(val)
            self.test_data.append(train)
            print(self.training_data, self.test_data)"""









        """
        self.F_BidsList.append(bid_prices[0])
        self.F_BidsList = self.F_BidsList[-5:]
        stdF_Bids = np.std(self.F_BidsList)
        meanF_Bids = np.mean(self.F_BidsList)

        if self.F_BidsList[-1] < meanF_Bids - stdF_Bids:
            if self.ask_id == 0 and self.position > -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, self.ETFData[-1][0][0]-stdF_Bids, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)
        """
            

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
        

