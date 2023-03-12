#https://github.com/aniket-rai/ready-trader-go-2022/blob/main/apple.py
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
import enum

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100

class Strategy(enum.IntEnum):
    SELL = 0
    BUY = 1
    HOLD = 2
    
class AutoTrader(BaseAutoTrader):
    """
    Apple - First attempt at Ichimoku.
    
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = 0

        self.__price_list = []
        self.__strategy = Strategy.BUY
        self.__position = 0
        # self.__active_bids = 0
        # self.__active_asks = 0
    
    def __update_price_list(self, new_price):
        if len(self.__price_list) == 78:
            self.__price_list.pop(0)
        self.__price_list.append(new_price)
        # self.logger.info(f'Updated Price List : {self.__price_list[:3]} ... {self.__price_list[-3:]} Length: {len(self.__price_list)}')

    def __get_rolling_average(self, data, WINDOW_SIZE):
        #data is a list of integers representing the last 78 prices.
        if len(data) < WINDOW_SIZE:
            pass #IMPLEMENT A FAILSAFE FOR THIS
        
        rolling_average_data = []
        for index in range(WINDOW_SIZE, len(data)):
            rolling_max = max(x for x in data[index - WINDOW_SIZE : index])
            rolling_min = min(x for x in data[index - WINDOW_SIZE : index])
            rolling_avg = round((rolling_max + rolling_min) / 2)
            rolling_average_data.append(rolling_avg)
    
        return rolling_average_data

    def __calculate_tenkan_sen(self):
        #The Conversion Line [(9D-High + 9D-Low) //2]
        temp = self.__get_rolling_average(self.__price_list, WINDOW_SIZE=9)
        tenkan_sen_data = [None for _ in range(9)] + temp + [None for _ in range(26)]
        return tenkan_sen_data
    
    def __calculate_kijun_sen(self):
        #The Base Line [(26D-High + 26D-Low) //2]
        temp = self.__get_rolling_average(self.__price_list, WINDOW_SIZE=26)
        kijun_sen_data = [None for _ in range(26)] + temp + [None for _ in range(26)]
        return kijun_sen_data
    
    def __calculate_chikou_span(self):
        #Lagging Span - 26 days backwards
        chikou_span_data = [round(x) for x in self.__price_list[26:]] + [None for _ in range(52)]
        return chikou_span_data

    def __calculate_senkou_span_a(self, tenkan, kijun):
        #Conversion and Base Midpoint, offset forwards by 26 (52 total)
        senkou_span_a_data = [None for _ in range(52)]
        for index in range(52, len(self.__price_list) + 26):
            senkou_span_a_data.append((tenkan[index - 26] + kijun[index - 26]) // 2)
        
        return senkou_span_a_data   

    def __calculate_senkou_span_b(self):
        #[(52D-High + 52D-Low) // 2], offset forwards by 26 (78 total)
        temp = self.__get_rolling_average(self.__price_list, WINDOW_SIZE=52)
        senkou_span_b_data = [None for _ in range(78)] + temp
        return senkou_span_b_data

    def __make_prediction(self):
        """" Buy Signals -
                Price above Cloud
                Cloud is Green
                Price Above Kijun/Base
                Tenkan/Conversion above Kijun/Base [Primary]

            -The current moment is located at index 78-

        """
        tenkan = self.__calculate_tenkan_sen()
        kijun = self.__calculate_kijun_sen()
        chikou = self.__calculate_chikou_span()
        ssa = self.__calculate_senkou_span_a(tenkan, kijun)
        ssb = self.__calculate_senkou_span_b()
        
        # for index, (t,k,c,a,b) in enumerate(zip(tenkan, kijun, chikou, ssa, ssb))[70:]:
        #     print(index, [t,k,c,a,b])

        current_price = self.__price_list[77]
        cloud = [value_a - value_b if value_a and value_b else None for value_a, value_b in zip(ssa[78:], ssb[78:])]
        # print(cloud)
        # cloud_range = sorted([ssa[78], ssb[78]]) #[lower, higher]

        #basic implementation
        signal = 0
        #Signal 1
        if  current_price > max(ssa[78], ssb[78]):
            signal +=1
            # print('Buy Signal 1')
        elif  current_price < min(ssa[78], ssb[78]):
            signal -=1
            # print('Sell Signal 1')
        else:
            pass
            # print('Hold until out of cloud')
        
        #Signal 2
        if all(x >= 0 for x in cloud[:13]):
            #13 is arbitrary half here
            signal +=1
            # print('Buy Signal 2')
        elif all(x <= 0 for x in cloud[:13]):
            signal -=1
            # print('Sell Signal 2')
        else:
            pass
            # print('Weird Market')
        
        #Signal 3
        if  current_price > kijun[77]:
            signal += 1
            # print('Buy Signal 3')
        elif  current_price < kijun[77]:
            signal -=1
            # print('Sell Signal 3')
        else:
            pass
            # print('Inconclusive Signal')
        
        #Main signal
        if tenkan[77] > kijun[77]:
            signal += 1
            # print('Main Buy Signal')
        elif tenkan[77] < kijun[77]:
            signal -= 1
            # print('Main Sell Signal')
        else:
            pass
            # print('This should just depend on current strategy')
        
        #Exit Signal
        # if chikou[51] < current_price:
        #     'Chikou below price'
        # else:
        #     'Chikou at or above price'

        confidence = round(abs(signal) / 4, 2)
        if signal < 0:
            return (Strategy.SELL, confidence)
        elif signal > 0:
            return (Strategy.BUY, confidence)
        else:
            return (Strategy.HOLD, confidence)

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        # self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled, partially or fully.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.

        If the order was unsuccessful, both the price and volume will be zero.
        """
        # self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id, price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        # self.logger.info("received order book for instrument %d with sequence number %d", instrument, sequence_number)
        if not bid_prices[0] and not ask_prices[0]:
            return
        if instrument == Instrument.FUTURE:
            new_bid_price = new_ask_price = 0
            self.__update_price_list(bid_prices[0])
            if len(self.__price_list) == 78: #78
                strategy = self.__make_prediction()

                if strategy[0] == Strategy.BUY:
                    new_bid_price = ask_prices[0] if ask_prices[0] != 0 else 0
                    new_ask_price = ask_prices[2] if ask_prices[2] != 0 else 0
                elif strategy[0] == Strategy.SELL:
                    new_ask_price = bid_prices[0] if bid_prices[0] != 0 else 0
                    new_bid_price = bid_prices[2] if bid_prices[2] != 0 else 0

                if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0): 
                    #only one bid at a time
                    self.logger.warning(f"Cancelling buy order {self.bid_id}")
                    self.send_cancel_order(self.bid_id)
                    self.bid_id = 0

                if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                    #only one ask at a time
                    self.logger.warning(f"Cancelling sell order {self.ask_id}")
                    self.send_cancel_order(self.ask_id)
                    self.ask_id = 0
                
                active_bids = sum([bid[2] for bid in self.bids])
                active_asks = sum([ask[2] for ask in self.asks])

                if self.bid_id == 0 and new_bid_price != 0 and self.__position + active_bids + LOT_SIZE < POSITION_LIMIT:
                    # self.logger.info(f'Placing a bid based off information POSITION: {self.__position} | ACTIVE_BIDS: {active_bids} | LOT_SIZE: {LOT_SIZE}')
                    self.bid_id = next(self.order_ids)
                    self.bid_price = new_bid_price
                    self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.bids.add((self.bid_id, new_bid_price, LOT_SIZE))

                if self.ask_id == 0 and new_ask_price != 0 and self.__position - active_asks - LOT_SIZE > -POSITION_LIMIT:
                    self.ask_id = next(self.order_ids)
                    self.ask_price = new_ask_price
                    self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.asks.add((self.ask_id, new_ask_price, LOT_SIZE))

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when when of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in [bid[0] for bid in self.bids]:
            self.__position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, self.ask_price, volume)
        elif client_order_id in [ask[0] for ask in self.asks]:
            self.__position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, self.bid_price, volume)

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
        
        #for a given id we check remaining_volume + filled_volume = bid_volume (not_cancelled)
        #else (cancelled)
        bid_order = next((result for result in self.bids if result[0] == client_order_id), None)
        ask_order = next((result for result in self.asks if result[0] == client_order_id), None)

        if bid_order:
            if remaining_volume == 0:
                self.bids.remove(bid_order)
            else:
                new_bid = (bid_order[0], bid_order[1], remaining_volume)
                self.bids.remove(bid_order)
                self.bids.add(new_bid)            
        elif ask_order:
            if remaining_volume == 0:
                self.asks.remove(ask_order)
            else:
                new_ask = (ask_order[0], ask_order[1], remaining_volume)
                self.asks.remove(ask_order)
                self.asks.add(new_ask)    
        else:
            # panic
            self.logger.critical("Order %d not found", client_order_id)
            pass

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        #self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument, sequence_number)