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


LOT_SIZE = 75
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

PLACE_ORDER_WAIT_TIME = 0.23 #Time delay before looping through scheduled_placing_in_spread()
MAX_ORDERS_LENGTH = 5 #Maximum order count = unused
BATCH_OF_ORDERS_ACTIVE = 10 #We wait until this many new orderbooks have been passed since trading before trading again. 
POSITION_BOUNDARY = 30 #Beyone +-this, we only take opposite trades ie: only sell if self.position > POSITION_BOUNDARY
TARGET_VOLUME_ON_EACH_SIDE = 10 #Target volume on bid and ask each time we fill the spread
MAX_UNHEDGED_LOTS = 10 #Max unhedged lots, used for keeping 
LATE_HEDGE_TIME = 55 #Wait this many secs after trading before testing if we need a hedge (alwasy keep this < 60)


class AutoTrader(BaseAutoTrader):
    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
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
        self.waitingVolume = 0
        self.messeges_in_s = 0
        self.orders = {}
        self.orderBookDataCount = 0
        self.lastTime = self.event_loop.time()
        self.lastHedgeCheckTime = self.event_loop.time()
        self.current_etf_ask_prices = self.current_etf_ask_volumes = self.current_etf_bid_prices = self.current_etf_bid_volumes = []
        self.current_fut_ask_prices = self.current_fut_ask_volumes = self.current_fut_bid_prices = self.current_fut_bid_volumes = []
        self.Future_best_bid = self.Future_best_ask = self.etf_best_bid = self.etf_best_ask = 0
    
    def calcOptimalPosition(self):
        if self.position == 0: return int(LOT_SIZE/MAX_ORDERS_LENGTH)
        size = abs(self.position)
        if size < 10: return 5
        else: return int(0.5 * size)
        
    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        self.logger.warning("error with order %d: %s and messeges: %d", client_order_id, error_message.decode(), self.messeges_in_s)
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,price, volume)
        if client_order_id in self.HEDGE_bids:
            self.HedgePosition += volume
        elif client_order_id in self.HEDGE_asks:
            self.HedgePosition -= volume
        

    def checkOneSecond(self):
        """Check whether last time this func ran was more than 1s ago, if it was reset messege count. We call this as often as possible."""
        now = self.event_loop.time()
        if now - self.lastTime >= 1.00:
            self.lastTime = now
            self.messeges_in_s = 0
        #print(self.messeges_in_s)
        
    def placeBuy(self, price:int, size = None):
        self.checkOneSecond()
        #print("BUY: ", self.waitingOrders, self.messeges_in_s, self.position)
        if self.waitingOrders >= 9 or self.messeges_in_s >= 25 or self.position >= POSITION_LIMIT-1:return
        self.bid_id = next(self.order_ids)
        self.bid_price = int(price)
        if size is None: size = self.calcOptimalPosition()
        if self.position + size >= POSITION_LIMIT-1: return
        #print("104", self.bid_id, Side.BUY, self.bid_price, size, Lifespan.GOOD_FOR_DAY)
        self.send_insert_order(self.bid_id, Side.BUY, self.bid_price, size, Lifespan.GOOD_FOR_DAY)
        self.messeges_in_s += 1
        self.waitingVolume += size
        self.waitingOrders += 1
        self.orders.update({self.bid_id:[size,self.bid_price]})
        self.bids.add(self.bid_id)

    def placeSell(self, price:int, size = None):
        self.checkOneSecond()
        #print("SELL:", self.waitingOrders, self.messeges_in_s, self.position)
        if self.waitingOrders >= 9 or self.messeges_in_s >= 25 or self.position <= -POSITION_LIMIT+1:return
        self.ask_id = next(self.order_ids)
        self.ask_price = int(price)
        if size is None: size = self.calcOptimalPosition()
        if self.position - size <= -POSITION_LIMIT+1: return
        #print("118", self.ask_id, Side.SELL, self.ask_price, size, Lifespan.GOOD_FOR_DAY)
        self.send_insert_order(self.ask_id, Side.SELL, self.ask_price, size, Lifespan.GOOD_FOR_DAY)
        self.messeges_in_s += 1
        self.waitingVolume += size
        self.waitingOrders += 1
        self.orders.update({self.ask_id:[size,self.ask_price]})
        self.asks.add(self.ask_id)
        

    def checkEqualOppPosition(self):#Test whether Hedge lots == - ETF lots
        if self.position == - self.HedgePosition: return
        else:
            self.logger.info("Unbalanced Trading: pos, fut: %d %d",self.position, self.HedgePosition)
    
    def loopThroughPossibleBuys(self, bid_to_mid_range):
        """Loop through array of prices (normally best_bids->mids) and place orders there"""
        trade_size = int(TARGET_VOLUME_ON_EACH_SIDE/(1+len(bid_to_mid_range)))
        for i, p in enumerate(bid_to_mid_range):
            if p < self.mid:#check, somtimes crosses occur
                #print("135", p-TICK_SIZE_IN_CENTS)
                self.placeBuy(p-TICK_SIZE_IN_CENTS, size = trade_size)
    
    def loopThroughPossibleSells(self, mid_to_ask_range):
        trade_size = int(TARGET_VOLUME_ON_EACH_SIDE/(1+len(mid_to_ask_range)))
        for i, p in enumerate(mid_to_ask_range):
            if p > self.mid:
                #print("142", p+TICK_SIZE_IN_CENTS)
                self.placeSell(p+TICK_SIZE_IN_CENTS, size = trade_size)

        
    def scheduled_placing_in_spread(self):#print(self.mid, self.spread, self.mid - self.spread, self.mid + self.spread, TICK_SIZE_IN_CENTS, np.arange(self.mid - self.spread, self.mid + self.spread, TICK_SIZE_IN_CENTS))
        self.checkOneSecond()

        if self.mid == 0 or self.spread <= 50 or self.spread >= 5000:return#occurs at edge cases
        
        bid_to_mid_range = np.arange(max(self.best_bid, self.mid-MAX_ORDERS_LENGTH*TICK_SIZE_IN_CENTS), self.mid, TICK_SIZE_IN_CENTS)
        if self.position < -POSITION_BOUNDARY:#if pos is less than POSITION_BOUNDARY, only buy
            self.loopThroughPossibleBuys(bid_to_mid_range)
        
        mid_to_ask_range = np.arange(self.mid, min(self.best_ask, self.mid+MAX_ORDERS_LENGTH*TICK_SIZE_IN_CENTS), TICK_SIZE_IN_CENTS)
        if self.position > POSITION_BOUNDARY:#if pos is more than POSITION_BOUNDARY, only sell
            self.loopThroughPossibleSells(mid_to_ask_range)
        
        elif abs(self.position) <= POSITION_BOUNDARY:
            if self.position > 0:
                self.loopThroughPossibleSells(mid_to_ask_range)
                self.loopThroughPossibleBuys(bid_to_mid_range)
            if self.position < 0:
                self.loopThroughPossibleBuys(bid_to_mid_range)
                self.loopThroughPossibleSells(mid_to_ask_range)
            elif self.position == 0 and len(mid_to_ask_range) != 0 and len(bid_to_mid_range) != 0:
                #If 0 pos, interleave bids and asks arrays to loop through. Doing bids then asks (one then the other) wil bias us as we max out our possible trades on one and cannot execute on the other. 
                trade_size = int(TARGET_VOLUME_ON_EACH_SIDE/(1+len(mid_to_ask_range)+len(bid_to_mid_range)))
                c = np.zeros(shape=(len(bid_to_mid_range)+len(mid_to_ask_range)))
                for i in range(max(len(mid_to_ask_range),len(bid_to_mid_range))):
                    try:c[i] = bid_to_mid_range[i]
                    except:pass
                    try:c[i+1] = mid_to_ask_range[i]
                    except:pass
                #print(bid_to_mid_range, mid_to_ask_range)
                #print(self.mid, c)
                for i, p in enumerate(c):
                    if p ==0: continue#p==0 when prices are empty at start, or somtimes when no midpoint exists
                    if p < self.mid:
                        #print("177BUY: ", p)
                        self.placeBuy(p-TICK_SIZE_IN_CENTS, size = trade_size)
                    if p > self.mid:
                        self.placeSell(p+TICK_SIZE_IN_CENTS, size = trade_size)

    def cancelling(self):#print("Cancelling: ", len(self.asks), len(self.bids))
        self.checkOneSecond()
        for i in self.asks:
            self.send_cancel_order(i)
            self.messeges_in_s += 1
        for i in self.bids:
            self.send_cancel_order(i)
            self.messeges_in_s += 1
        
    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received order book for instrument %d with sequence number %d, waitingOrders %d, waitingVolume %d, messeges %d", instrument,sequence_number,self.waitingOrders,self.waitingVolume, self.messeges_in_s)
        self.checkOneSecond()
        self.testHedgeNeeded()
        #self.checkEqualOppPosition()

        if instrument == Instrument.ETF:
            #print("on_order_book_update_m", instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)
            self.spread = abs(min(ask_prices[0] - bid_prices[0], 5000))
            self.best_ask = ask_prices[0]
            self.best_bid = bid_prices[0]
            self.current_etf_ask_prices = list(ask_prices)
            self.current_etf_ask_volumes = list(ask_volumes)
            self.current_etf_bid_prices = list(bid_prices)
            self.current_etf_bid_volumes = list(bid_volumes)
            if ask_prices[0] < self.etf_best_ask:self.etf_best_ask = ask_prices[0]
            if bid_prices[0] > self.etf_best_bid:self.etf_best_bid = bid_prices[0]

        if instrument == Instrument.FUTURE:
            if self.orderBookDataCount >= BATCH_OF_ORDERS_ACTIVE:
                #if its been BATCH_OF_ORDERS_ACTIVE since we traded last, cancel all active orders.
                self.cancelling()
                self.orderBookDataCount = -1
            self.orderBookDataCount += 1
            self.mid = int(np.round((ask_prices[0] - bid_prices[0])/2 + bid_prices[0], -2))
            #in PLACE_ORDER_WAIT_TIME secs, execute self.scheduled_placing_in_spread
            self.loop.call_at(time.time() + PLACE_ORDER_WAIT_TIME, self.scheduled_placing_in_spread())
            self.current_fut_ask_prices = list(ask_prices)
            self.current_fut_ask_volumes = list(ask_volumes)
            self.current_fut_bid_prices = list(bid_prices)
            self.current_fut_bid_volumes = list(bid_volumes)
            if ask_prices[0] < self.Future_best_ask:self.Future_best_ask = ask_prices[0]
            if bid_prices[0] > self.Future_best_bid:self.Future_best_bid = bid_prices[0]

            if self.waitingOrders == self.waitingOrders == 0 and abs(self.position) > 10:
                #This is a situation where we're sat in a large trade with no exit plan - could optimize with curating an exit strategy.
                #If we're not in a trade we should look for arbitrage opportunities.
                pass

    def testHedgeNeeded(self):
        """
        NOT 100% CORRECT YET
        Need to add point at which self.position changed signs, thats what the time measurement is from.
        We wait as long as possible before headging, saves us costs and can make us directionally trading too. Saw profit improve.
        """
       
        now = self.event_loop.time()
        print("Checking Hedge Time: ", np.round(now - self.lastHedgeCheckTime), " >= ", LATE_HEDGE_TIME)
        if now - self.lastHedgeCheckTime >= LATE_HEDGE_TIME:
            self.lastHedgeCheckTime = now
            targetHedgePosition = -1 * self.position
            sizeDiff = targetHedgePosition - self.HedgePosition
            print("Size Diff: ", sizeDiff)
            if abs(sizeDiff) >= MAX_UNHEDGED_LOTS:
                print("pos, hedgePos, target; ", self.position, self.HedgePosition, targetHedgePosition)
                if sizeDiff < 0:
                    id = next(self.order_ids)
                    self.send_hedge_order(id, Side.ASK, MIN_BID_NEAREST_TICK, abs(sizeDiff))
                    self.messeges_in_s += 1
                    self.HEDGE_asks.add(id)
                    #print(self.position, self.HedgePosition, id, Side.ASK, MIN_BID_NEAREST_TICK, sizeDiff)
                if sizeDiff > 0:
                    id = next(self.order_ids)
                    self.send_hedge_order(id, Side.BID, MAX_ASK_NEAREST_TICK, abs(sizeDiff))
                    self.messeges_in_s += 1
                    self.HEDGE_bids.add(id)
                    #print(self.position, self.HedgePosition, id, Side.BID, MAX_ASK_NEAREST_TICK, sizeDiff)
 

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,price, volume)
        self.checkOneSecond()
        if client_order_id in self.bids:
            if np.sign(self.position + volume) != np.sign(self.position):
                self.lastHedgeCheckTime = self.event_loop.time()
            self.loop.call_at(time.time() + LATE_HEDGE_TIME, self.testHedgeNeeded())
            self.position += volume
            """Hashed parts are where I added the delayed hedging. Remove #'s and the - loop.call_a ... estHedgeNeeded()) - to change back to immediate hedgeing."""
            #id = next(self.order_ids)
            #self.send_hedge_order(id, Side.ASK, MIN_BID_NEAREST_TICK, volume)
            #self.messeges_in_s += 1
            #self.HEDGE_asks.add(id)
            #if abs(self.position - self.HedgePosition) > MAX_UNHEDGED_LOTS:
            #    self.loop.call_at(time.time() + LATE_HEDGE_TIME, self.testHedgeNeeded())
            if self.etf_best_ask > price:#Search for arb opportunity
                self.placeSell(self.etf_best_ask + TICK_SIZE_IN_CENTS, volume)

        elif client_order_id in self.asks:
            if np.sign(self.position - volume) != np.sign(self.position):
                self.lastHedgeCheckTime = self.event_loop.time()
            self.loop.call_at(time.time() + LATE_HEDGE_TIME, self.testHedgeNeeded())
            self.position -= volume
            #id = next(self.order_ids)
            #self.send_hedge_order(id, Side.BID, MAX_ASK_NEAREST_TICK, volume)
            #self.messeges_in_s += 1
            #self.HEDGE_bids.add(id)
            #if abs(self.position - self.HedgePosition) < -MAX_UNHEDGED_LOTS:
            #    self.loop.call_at(time.time() + LATE_HEDGE_TIME, self.testHedgeNeeded())
            if self.etf_best_bid < price:#Search for arb opportunity
                self.placeBuy(self.etf_best_bid - TICK_SIZE_IN_CENTS, volume)
        if self.position == 0: self.lastHedgeCheckTime = self.event_loop.time()
    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,fees: int) -> None:
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",client_order_id, fill_volume, remaining_volume, fees)
        self.checkOneSecond()

        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            #Order is cancelled, we need to remove its volume from our waitingVol variable.
            self.waitingOrders -= 1
            self.waitingVolume -= self.orders[client_order_id][0]
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        self.logger.info("received order book for instrument %d with sequence number %d, waitingOrders %d, waitingVolume %d, messeges %d", instrument,sequence_number,self.waitingOrders,self.waitingVolume, self.messeges_in_s)
        self.checkOneSecond()
        if instrument == Instrument.ETF:   
            if ask_prices[0] < self.etf_best_ask:self.etf_best_ask = ask_prices[0]
            if bid_prices[0] > self.etf_best_bid:self.etf_best_bid = bid_prices[0] 
        if instrument == Instrument.FUTURE:
            if ask_prices[0] < self.Future_best_ask:self.Future_best_ask = ask_prices[0]
            if bid_prices[0] > self.Future_best_bid:self.Future_best_bid = bid_prices[0]