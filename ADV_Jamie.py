import asyncio
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side

import numpy as np

###NOT ALLOWED IN THE COMPETITION
import time
#import traceback
###

LOT_SIZE = 10
POSITION_LIMIT = 200 #1000 #-1000 -> 1000
VOLUME_LIMIT = 200 #-200 -> 200
TICK_SIZE_IN_CENTS = 1
MESSAGE_LIMIT = 50 #Per turn
ACTIVE_ORDER_LIMIT = 10 #generally

#OPCOUNT = int(min(ACTIVE_ORDER_LIMIT, ((MESSAGE_LIMIT/6)//2))) #4 ticks a turn, //2 BECAUSE A TURN OCCURS TWICE A SECOND
#print("OPCOUNT", OPCOUNT)

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
        self.orders = {} #{123:[age, volume, fulfilled]}
        self.deadOrders = {}
        self.ids = itertools.count(1)
        self.pos = {"unreal":[0,0], "real":0} #avg price, unreal volume (buy,sell), realised volume
        #self.trail = []
        #self.priceTrail = []
        self.data = []
        self.midPrices = [[],[]]
        self.instOffsets = []
        self.accuracy = []
        self.times = []
        self.sequence_numbers = []
        self.commstally = 0
        self.midPriceOffsets = []

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.
        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            #print("ERROR FROM EXCHANGE:", error_message)
            #self.on_order_status_message(client_order_id, 0, 0, 0)
            self.cull_order(client_order_id,False)

    def cull_order(self, id, d=True):
        if id in self.orders.keys() and self.commstally <= MESSAGE_LIMIT:
            self.commstally += 1
            if d:
                self.send_cancel_order(id) #This is only needed if Lifespan.G is used as otherwise the trades are automatically closed
            if self.orders[id][1] > 0:
                self.pos["unreal"][0] -= self.orders[id][1] - self.orders[id][2]
            else:
                self.pos["unreal"][1] -= self.orders[id][1] - self.orders[id][2]
            self.deadOrders[id] = self.orders[id].copy()
            del self.orders[id]
        #elif self.commstally > MESSAGE_LIMIT:
            #print("MESSAGE LIMIT HIT.")

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int], ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None: #4 sequence numbers = a second. (eg: 0,1,2,3 would cover a second)
        """Called periodically to report the status of an order book.
        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        #instrument 0 is FUTURE
        startTime = time.perf_counter()
        try:
            ask_prices,ask_volumes,bid_prices,bid_volumes = np.array(ask_prices),np.array(ask_volumes),np.array(bid_prices),np.array(bid_volumes)

            nonzeroind = np.nonzero(bid_volumes)
            bid_volumes = bid_volumes[nonzeroind]
            bid_prices = bid_prices[nonzeroind]
            nonzeroind = np.nonzero(ask_volumes)
            ask_volumes = ask_volumes[nonzeroind]
            ask_prices = ask_prices[nonzeroind]

            if len(ask_volumes) > 0 and len(bid_volumes) > 0 and self.sequence_numbers.count(sequence_number) in [0,1]:
                self.midPrices[instrument].append((bid_prices[0] + ask_prices[0]) / 2) #Mid price
                self.sequence_numbers.append(sequence_number)
                self.data.append([instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes])
                if instrument == 1: #RECEIVED ALL
                    minprice,maxprice = bid_prices[-1],ask_prices[-1]
                    if len(self.orders.keys()) > 0:
                        minprice = min(self.orders.items(), key=lambda x: x[1][3])[1][3]
                        maxprice = max(self.orders.items(), key=lambda x: x[1][3])[1][3]
                    book_prices = np.arange(min(bid_prices[-1],minprice), max(ask_prices[-1],maxprice)+1)
                    book_volumes = {}
                    for i in range(len(book_prices)):
                        book_volumes[book_prices[i]] = []
                    for i in range(len(ask_volumes)):
                        book_volumes[ask_prices[i]].append(-ask_volumes[i])
                    for i in range(len(bid_volumes)):
                        book_volumes[bid_prices[i]].append(bid_volumes[i])
                    for order in self.orders.items():
                        if not bid_prices[-1] <= order[1][3] <= ask_prices[-1]:
                            book_volumes[order[1][3]].append(order[1][1])

                    probs = []
                    wholeRange = len(book_prices)

                    s = np.array([np.sum(np.abs(v)) for v in book_volumes.values()])
                    s = 1 - ((s - np.amin(s)) / (np.amax(s) - np.amin(s)))

                    midPrice = self.midPrices[1][-1]
                    d = [np.abs(midPrice - p) for p in book_volumes.keys()]
                    d = 1 - ((d - np.amin(d)) / (np.amax(d) - np.amin(d)))

                    # offset = self.midPrices[0][-1] - self.midPrices[1][-1]
                    # self.midPriceOffsets.append(offset)
                    # o = (offset - np.amin(self.midPriceOffsets)) / (np.amax(self.midPriceOffsets) - np.amin(self.midPriceOffsets))

                    trdprofits = []
                    for p in range(wholeRange):
                        if book_prices[p] > midPrice:
                            trdprofits.append(-book_prices[p] + self.midPrices[0][-1])
                        elif book_prices[p] <= midPrice:
                            trdprofits.append(book_prices[p] - self.midPrices[0][-1])
                    trdprofits = np.array(trdprofits)
                    trdprofits = (trdprofits - np.amin(trdprofits)) / (np.amax(trdprofits) - np.amin(trdprofits))

                    # if np.isnan(o):
                    #     oW = np.ones(wholeRange)
                    # else:
                    #     oW = (np.linspace(1-o,o,wholeRange))
                    for p in range(wholeRange):
                        if book_prices[p] > midPrice:
                            if np.sum(book_volumes[book_prices[p]]) <= 0:
                                probs.append(-np.average([s[p], d[p], trdprofits[p]], weights=[1,2,1])) #weight for volume, weight for proximity to mid, weight for prediction
                            else:
                                probs.append(0)
                        elif book_prices[p] <= midPrice:
                            if np.sum(book_volumes[book_prices[p]]) >= 0:
                                probs.append(np.average([s[p], d[p], trdprofits[p]], weights=[1,2,1])) #weight for volume, weight for proximity to mid, weight for prediction #+ oW[p]
                            else:
                                probs.append(0)
                    #probs = (((probs - np.amin(probs)) / (np.amax(probs) - np.amin(probs))) * 2) - 1

                    book_myvolumes = [0 for i in range(wholeRange)]
                    book_ids = [None for i in range(wholeRange)]
                    lowestPrice = bid_prices[-1]
                    for id in self.orders.keys():
                        if int(self.orders[id][3]) in book_prices:
                            ind = int(self.orders[id][3] - lowestPrice)
                            book_myvolumes[ind] += (self.orders[id][1] - self.orders[id][2])
                            book_ids[ind] = id
                    book_myvolumes = np.array(book_myvolumes)
                    book_ids = np.array(book_ids)
                    book_absMyvolumes = np.abs(book_myvolumes)
                    if sum(book_absMyvolumes) > 0:
                        book_myportfolio = book_myvolumes / np.amax(book_absMyvolumes)
                    else:
                        book_myportfolio = book_myvolumes
                    book_deltas = (probs - book_myportfolio) / 2 #/2 to normalise [-1,1]
                    book_absDeltas = np.abs(book_deltas)
                    ind = np.argpartition(book_absDeltas, -ACTIVE_ORDER_LIMIT)[-ACTIVE_ORDER_LIMIT:]
                    ind = ind[np.argsort(book_absDeltas[ind])]
                    book_closeDeltas = np.array([book_deltas[p] if book_myportfolio[p] != 0 else 0 for p in range(wholeRange)])
                    book_absCloseDeltas = np.abs(book_closeDeltas)
                    cInd = np.argpartition(book_absCloseDeltas, -ACTIVE_ORDER_LIMIT)[-ACTIVE_ORDER_LIMIT:]
                    cInd = cInd[np.argsort(book_absCloseDeltas[cInd])]

                    #cInd, ind = cInd[::-1], ind[::-1]

                    # cutoff = ACTIVE_ORDER_LIMIT
                    # for i in range(ACTIVE_ORDER_LIMIT-1,-1,-1):
                    #     if book_absCloseDeltas[cInd[i]] > book_absDeltas[ind[i]]:
                    #         cutoff = i
                    #     else:
                    #         break
                    # cInd,ind = cInd[cutoff:][::-1], ind[max(0,cutoff-(ACTIVE_ORDER_LIMIT-len(self.orders.keys()))):][::-1]

                    #print(book_ids[ind], np.round(book_deltas[ind],2), book_ids[cInd], np.round(book_closeDeltas[cInd],2))

                    book_volume = self.pos["unreal"][0] - self.pos["unreal"][1]
                    posOpSum = np.sum(book_deltas[ind][book_deltas[ind] > 0])
                    negOpSum = -np.sum(book_deltas[ind][book_deltas[ind] < 0])
                    lims = np.clip([self.pos["unreal"][1] + self.pos["real"] + POSITION_LIMIT, POSITION_LIMIT - (self.pos["unreal"][0] + self.pos["real"])],0,VOLUME_LIMIT)
                    limsSum = np.sum(lims)
                    maxsell = lims[0]
                    maxbuy = lims[1]
                    if maxbuy + maxsell > 0:
                        maxbuy *= (VOLUME_LIMIT - book_volume) / limsSum
                        maxsell *= (VOLUME_LIMIT - book_volume) / limsSum
                    if posOpSum > 0:
                        book_posOpRatio = maxbuy / posOpSum
                    if negOpSum > 0:
                        book_negOpRatio = maxsell / negOpSum

                    ###ORDER ENGINE
                    for i in ind:
                        if self.commstally >= MESSAGE_LIMIT / 3:
                            break
                        orderid = book_ids[i]

                        if i != None and orderid in self.orders:
                            if book_deltas[i] < 0:
                                vol = int(book_deltas[i] * book_negOpRatio)
                                #print(self.orders[orderid][1], "MODIFY", (self.orders[orderid][1] - self.orders[orderid][2]), "TO", vol, "AT", book_prices[i])
                                self.cull_order(orderid)
                            elif book_deltas[i] > 0:
                                vol = int(book_deltas[i] * book_posOpRatio)
                                #print(self.orders[orderid][1], "MODIFY", (self.orders[orderid][1] - self.orders[orderid][2]), "TO", vol, "AT", book_prices[i])
                                self.cull_order(orderid)
                        else:
                            ###DELETION
                            #print(self.pos, book_volume)
                            book_volume = self.pos["unreal"][0] - self.pos["unreal"][1]
                            if ((book_volume / VOLUME_LIMIT) > 0.99 or len(self.orders.keys()) == ACTIVE_ORDER_LIMIT) and len(cInd) > 0:
                                ci, cInd = cInd[-1], cInd[:-1]
                                orderid_delete = book_ids[ci]
                                self.cull_order(orderid_delete)
                            lims = np.clip([self.pos["unreal"][1] + self.pos["real"] + POSITION_LIMIT, POSITION_LIMIT - (self.pos["unreal"][0] + self.pos["real"])],0,VOLUME_LIMIT)
                            limsSum = np.sum(lims)
                            maxsell = lims[0]
                            maxbuy = lims[1]
                            if maxbuy + maxsell > 0:
                                maxbuy *= (VOLUME_LIMIT - book_volume) / limsSum
                                maxsell *= (VOLUME_LIMIT - book_volume) / limsSum
                            if posOpSum > 0:
                                book_posOpRatio = maxbuy / posOpSum
                            if negOpSum > 0:
                                book_negOpRatio = maxsell / negOpSum
                            if book_deltas[i] < 0:
                                vol = int(book_deltas[i] * book_negOpRatio)
                                if vol < 0:
                                    ###INSERTION PREP
                                    orderid = next(self.ids)
                                    self.commstally += 1
                                    price = int(book_prices[i])
                                    negOpSum += book_deltas[i]
                                    ###EXECUTION
                                    self.orders[orderid] = [orderid, vol, 0, price] #age, volume, fulfilled, price
                                    self.pos["unreal"][1] += vol
                                    self.send_insert_order(orderid, Side.SELL, price, -vol, Lifespan.GOOD_FOR_DAY)
                                    self.send_hedge_order(next(self.order_ids), Side.BID, price, vol)
                                    #print("SELL", vol, "AT", book_prices[i])
                            elif book_deltas[i] > 0:
                                vol = int(book_deltas[i] * book_posOpRatio)
                                if vol > 0:
                                    ###INSERTION PREP
                                    orderid = next(self.ids)
                                    self.commstally += 1
                                    price = int(book_prices[i])
                                    posOpSum -= book_deltas[i]
                                    ###EXECUTION
                                    self.orders[orderid] = [orderid, vol, 0, price] #age, volume, fulfilled, price
                                    self.pos["unreal"][0] += vol
                                    self.send_insert_order(orderid, Side.BUY, price, vol, Lifespan.GOOD_FOR_DAY)
                                    self.send_hedge_order(next(self.order_ids), Side.ASK, price, -vol)
                                    #print("BUY", vol, "AT", book_prices[i])

                    self.times.append(time.perf_counter() - startTime)
                    if (sequence_number+1) % 4 == 0:
                        #print("OLD PERF", np.round(1/np.mean(self.times),3))
                        #print("PERF", np.mean(self.times), "POSITION", self.pos, "BOOK VOLUME", book_volume, "COMMSTALLY", self.commstally)
                        #print("ACCURACY", np.round(np.mean(self.accuracy),2), "FORECAST", np.round(self.instOffsets[-1],2), "ORDERS", len(self.orders.keys()), "OPCOUNT", OPCOUNT, "POSITION", self.pos, "COMMSTALLY", self.commstally, "TIME", sequence_number/4, "PERF", np.mean(self.times))
                        self.commstally = 0
                    ###REFRESH DATA FOR NEXT TURN
                    self.data = []

        except Exception as e:
            pass
            #print(traceback.format_exc())
            #print(e)
            #x = input()

        #normPrices = prices - avgPrice
        #A comparison could be made between stored order books (normalized), then outcomes from similar scenarios on the markov chain can be projected to predict future order books.

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when when of your orders is filled, partially or fully.
        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.
        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.
        If an order is cancelled its remaining volume will be zero.
        """
        if client_order_id in self.orders.keys() and fill_volume != self.orders[client_order_id][2]:
            info = self.orders[client_order_id]
            if info[1] > 0: #IF THE ORDER IS A BUY
                self.pos["unreal"][0] -= fill_volume - self.orders[client_order_id][2]
                self.pos["real"] += fill_volume - self.orders[client_order_id][2]
                self.orders[client_order_id][2] = fill_volume
            else: #IF THE ORDER IS A SELL
                self.pos["unreal"][1] -= -fill_volume - self.orders[client_order_id][2]
                self.pos["real"] += -fill_volume - self.orders[client_order_id][2]
                self.orders[client_order_id][2] = -fill_volume
        elif client_order_id in self.deadOrders.keys() and fill_volume != self.deadOrders[client_order_id][2]:
            info = self.deadOrders[client_order_id]
            if info[1] > 0: #IF THE ORDER IS A BUY
                #self.pos["unreal"][0] -= fill_volume - self.deadOrders[client_order_id][2]
                self.pos["real"] += fill_volume - self.deadOrders[client_order_id][2]
                self.deadOrders[client_order_id][2] = fill_volume
            else: #IF THE ORDER IS A SELL
                #self.pos["unreal"][1] -= -fill_volume - self.deadOrders[client_order_id][2]
                self.pos["real"] += -fill_volume - self.deadOrders[client_order_id][2]
                self.deadOrders[client_order_id][2] = -fill_volume
        if client_order_id in self.orders.keys() and remaining_volume == 0:
            self.deadOrders[client_order_id] = self.orders[client_order_id].copy()
            del self.orders[client_order_id]