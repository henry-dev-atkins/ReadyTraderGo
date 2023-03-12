#C:\Users\encha\AppData\Local\Programs\Python\Python311\python.exe rtg.py run ADV_Weiner.py

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
np.set_printoptions(suppress=True)
import scipy
from scipy import signal
import time

LOT_SIZE = 100
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
        self.InTrade = 0
        self.asksAdded = []
        self.bidsAdded = []
        self.convdepth = 200
        self.memory = np.zeros((2,self.convdepth,22))
        self.OrdersOpen = 0
        self.ourTick = -1
        self.old_bid_prices = []
        self.old_ask_prices = []
        self.old_midpointdiffs = []
        self.old_midpoints = []

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
    
    def makeStationaryVector(self,convwidth,bid_volumes,bid_prices,ask_volumes,ask_prices,midpointdiff):
        if (ask_prices[0] - bid_prices[0]) >= 2:
            bid_volumes = np.flip(np.array(bid_volumes))
            ask_volumes = np.array(ask_volumes)

            bid_prices = np.flip(np.array(bid_prices))
            ask_prices = np.array(ask_prices)

            prices = np.concatenate([bid_prices,ask_prices])
            volumes = np.concatenate([bid_volumes,ask_volumes])

            convdata = np.zeros(((convwidth//100)*2))
            ind = (prices-(bid_prices[-1]-convwidth))//100
            ind = ind + int(((midpointdiff)//100))
            indValid = np.logical_and(0 <= ind, ind < (convwidth//100)*2)
            convdata[ind[indValid]] = volumes[indValid]

            convdata = np.insert(convdata, (convwidth//TICK_SIZE_IN_CENTS)-1, np.array([0,0]))

            return convdata

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        if instrument == 1:
            self.ourTick += 1
        
        self.old_bid_prices.append(bid_prices)
        self.old_ask_prices.append(ask_prices)

        if self.ourTick == 0:
            self.old_midpoints.append(None)
            self.old_midpointdiffs.append(None)
        elif self.ourTick > 0:
            midpoint = ((self.old_bid_prices[-1][0]) + (self.old_ask_prices[-1][0])) / 2
            midpointdiff = ((self.old_bid_prices[-1][0] - self.old_bid_prices[-2][0]) + (self.old_ask_prices[-1][0] - self.old_ask_prices[-2][0])) / 2
            self.old_midpoints.append(midpoint)
            self.old_midpointdiffs.append(midpointdiff)
            convdata =self.makeStationaryVector(1000,bid_volumes,bid_prices,ask_volumes,ask_prices,midpointdiff)
            
            self.memory[instrument] = np.roll(self.memory[instrument], -1, axis=0)
            self.memory[instrument,-1] = convdata

        if self.ourTick >= 19:

            #wiener filter
            normmem = self.memory
            normmem = normmem / ((np.std(normmem,axis=(1))[:,np.newaxis,:]) + 0.0000001)
            ac = signal.fftconvolve(normmem,np.flip(normmem,axis=1),axes=1)
            #ac = ac / np.std(normmem**2,axis=1)[:,np.newaxis,:]
            conv = signal.fftconvolve(ac, self.memory, axes=1)
            conv[np.isnan(conv)] = 0
            conv *= np.mean(self.memory / (conv[:,self.convdepth-1:self.convdepth*2-1,:]+0.0000001),axis=1)[:,np.newaxis,:]

            if self.position == 0:
                self.InTrade = 0
            
            if instrument == 1: #ETF
                
                for i in self.asksAdded:
                    if i in self.asks:
                        self.send_cancel_order(i)
                        self.asks_id = 0
                for i in self.bidsAdded:
                    if i in self.bids:
                        self.send_cancel_order(i)
                        self.bids_id = 0
                self.asksAdded = []
                self.bidsAdded = []
                #time.sleep(0.1)
                pred = np.round(conv[1,self.convdepth*2-1])
                print(pred)
                #print(self.OrdersOpen == 0 and self.InTrade == 0 and bid_prices[0] != 0 or ask_prices[0] != 0 and abs(self.position) < POSITION_LIMIT)
                if self.OrdersOpen == 0 and self.InTrade == 0 and bid_prices[0] != 0 or ask_prices[0] != 0 and abs(self.position) < LOT_SIZE:
                    TOTAL = max(LOT_SIZE - abs(self.position), 0)
                    BID_TOTAL_SIZE = (TOTAL / 2) if self.position > 0 else (TOTAL / 2) - self.position
                    ASK_TOTAL_SIZE = (TOTAL / 2) if self.position < 0 else (TOTAL / 2) + self.position
                    bids = convdata[:11]
                    bids = np.flip(bids)
                    offset = int(self.old_midpointdiffs[-1]//100)
                    if abs(offset) < 5: #movement in the midpoint
                        total = np.sum(pred[:11+offset][-11:][(bids==0)[:-offset]])
                        if total > 0:
                            for i, vol in enumerate(bids[:-offset]): #bids
                                if ~np.isnan(vol) and vol == 0 and ~np.isnan(pred[i]) and pred[i] > 0:
                                    price = (self.old_bid_prices[-2][0] + self.old_midpointdiffs[-1]) - (i-1)*TICK_SIZE_IN_CENTS
                                    self.bid_id = next(self.order_ids)
                                    size = int(np.round(BID_TOTAL_SIZE * pred[i]/total))
                                    if size > 0 and size < 100:
                                        #print(self.bid_id, Side.BUY, price, size, Lifespan.GOOD_FOR_DAY)
                                        self.send_insert_order(self.bid_id, Side.BUY, price, size, Lifespan.GOOD_FOR_DAY)
                                        self.bids.add(self.bid_id)
                                        self.bidsAdded.append(self.bid_id)
                                        self.OrdersOpen += 1
                                        self.InTrade = 1
                                    if i >= 3: break
                        asks = convdata[11:]
                        total = np.sum(pred[11+offset:][-11:][(asks==0)[:-offset]])
                        if total > 0:
                            for i, vol in enumerate(asks[:-offset]): #Asks
                                if ~np.isnan(vol) and vol == 0 and ~np.isnan(pred[i+11+offset]) and pred[i+11+offset] > 0:
                                    price = (self.old_bid_prices[-2][0] + self.old_midpointdiffs[-1]) - (12+i) * TICK_SIZE_IN_CENTS
                                    #print(ask_prices[0], (10+i) , TICK_SIZE_IN_CENTS)
                                    self.ask_id = next(self.order_ids)
                                    size = int(np.round(ASK_TOTAL_SIZE * pred[i+11+offset]/total))
                                    if size > 0 and size < 100:
                                        print(self.ask_id, Side.SELL, price,  size, Lifespan.GOOD_FOR_DAY)
                                        self.send_insert_order(self.ask_id, Side.SELL, price,  size, Lifespan.GOOD_FOR_DAY)
                                        self.asks.add(self.ask_id) 
                                        self.asksAdded.append(self.ask_id)
                                        self.OrdersOpen += 1
                                        self.InTrade = 1
                                    if i >= 3: break

        """                 
        if instrument == 1 and bid_prices[0] != 0:  
            if self.position >= POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, ask_prices[0] + TICK_SIZE_IN_CENTS, POSITION_LIMIT, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id) 
                self.asksAdded.append(self.ask_id)
                self.OrdersOpen += 1
            if self.position <= -POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                #print(self.bid_id, Side.BUY, bid_prices[0] - TICK_SIZE_IN_CENTS, POSITION_LIMIT, Lifespan.GOOD_FOR_DAY)
                self.send_insert_order(self.bid_id, Side.BUY, bid_prices[0] - TICK_SIZE_IN_CENTS, POSITION_LIMIT, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)
                self.bidsAdded.append(self.bid_id)
                self.OrdersOpen += 1
                self.InTrade = 1


                          
            if self.InTrade != 0 and self.OrderBooksSinceTrade > 20:
                if self.position > 0:#short etf, buy future
                    self.ask_id = next(self.order_ids)
                    self.send_insert_order(self.ask_id, Side.SELL, ask_prices[0], LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.asks.add(self.ask_id)
                    id = next(self.order_ids)
                    #print("osition > 0, self.Future_best_ask < self.etf_best_bid", str(id) + str(self.self.Future_best_ask))
                    self.send_hedge_order(id, Side.BID, ask_prices[0], LOT_SIZE)
                    print("Was Long, Close by Sell")

                if self.position < 0: #short future, buy etf
                    self.bid_id = next(self.order_ids)
                    self.send_insert_order(self.bid_id, Side.BUY, bid_prices[0], LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.bids.add(self.bid_id)
                    id = next(self.order_ids)
                    #print("self.Future_best_bid > self.etf_best_ask, on < 0", str(id) + str(self.Future_best_bid))
                    self.send_hedge_order(id, Side.ASK, bid_prices[0], LOT_SIZE)
                    print("Was Short, Closing By Buying")
       
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        if instrument == Instrument.FUTURE:
            price_adjustment = - (self.position // LOT_SIZE) * TICK_SIZE_IN_CENTS
            new_bid_price = bid_prices[0] + price_adjustment if bid_prices[0] != 0 else 0
            new_ask_price = ask_prices[0] + price_adjustment if ask_prices[0] != 0 else 0

            if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0
            if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0

            if self.bid_id == 0 and new_bid_price != 0 and self.position < POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_price
                self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)

            if self.ask_id == 0 and new_ask_price != 0 and self.position > -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_price
                self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
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
        print("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        self.OrdersOpen -= 1
        if client_order_id in self.bids:
            self.position += volume
            if volume !=0:self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            if volume !=0:self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

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
                self.OrdersOpen -= 1
            elif client_order_id == self.ask_id:
                self.ask_id = 0
                self.OrdersOpen =- 1

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