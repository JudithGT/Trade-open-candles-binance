#!/usr/bin/python3

# DOC: https://binance-docs.github.io/apidocs/futures/en/#continuous-contract-kline-candlestick-data
# Usage: python3 liquidity.py --pair XMR --quantity 10 --interval HOUR --leverage 2 
import sys
import requests
import time
import argparse
import os

from datetime import datetime
from binance_f import RequestClient
from binance_f.constant.test import *
from binance_f.base.printobject import *
from binance_f.model.constant import *
from decimal import Decimal
from dotenv import load_dotenv
from enum import Enum
from simple_chalk import yellow, red, green, white

load_dotenv()

API_KEY = os.environ.get('API_KEY')
SECRET_KEY = os.environ.get('SECRET_KEY')

MAX_ORDER_RETRIES = 3

SLEEP_TIMEOUT = 15
START_INTERVAL = 0
END_INTERVAL = 8

MAX_STOP_LOSS_RISK = 3

INITIAL_DELAY = False

# Futures environment variables
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
BINANCE_FUTURES_KLINES_ENDPOINT = "/fapi/v1/continuousKlines"
BINANCE_FUTURES_EXCHANGE_INFO_ENDPOINT = "/fapi/v1/exchangeInfo"

# Spot environment variables
BINANCE_SPOT_BASE_URL = "https://api.binance.com"
BINANCE_SPOT_CREATE_ORDER_ENDPOINT = "/api/v3/order/test"
BINANCE_SPOT_KLINES_ENDPOINT = "/api/v3/klines"
BINANCE_SPOT_EXCHANGE_INFO_ENDPOINT = "/api/v3/exchangeInfo"

TIMES_GREEN = 0
TIMES_RED = 0

LAST_CANDLE_RED = True
LAST_CANDLE_GREEN = True

LAST_LOW_PRICE = 999999
LAST_HIGH_PRICE = 0

STOP_LOSS_REACHED = False
STOP_LOSS = 0

TARGET_REACHED = False
TARGET = 99999

STOP_LOSS_ORDER_ID = None
TAKE_PROFIT_ORDER_ID = None

class Intervals(Enum):
    #FIVETEEN_MINUTES = "15m"
    #THIRTY_MINUTES = "30m"
    HOUR = "1h"
    FOUR_HOURS = "4h"
    TWELVE_HOURS = "12h"
    DAY = "1d"
    THREE_DAYS = "3d"
    WEEK = "1w"
    TWO_WEEKS = "2w"
    MONTH = "1M"

    def __str__(self):
        return self.name.lower()

    @staticmethod
    def from_string(s):
        try:
            return Intervals[s.upper()]
        except KeyError:
            raise ValueError()

class SpotSides(Enum):
    BUY = 'buy'
    SELL = 'sell'

    def __str__(self):
        return self.name.lower()

    @staticmethod
    def from_string(s):
        try:
            return SpotSides[s.upper()]
        except KeyError:
            raise ValueError()

class Markets(Enum):
    FUTURES = 'futures'
    SPOT = 'spot'

    def __str__(self):
        return self.name.lower()

    @staticmethod
    def from_string(s):
        try:
            return Markets[s.upper()]
        except KeyError:
            raise ValueError()

class MarketSide(Enum):
    LONG = 'long'
    SHORT = 'short'

    def __str__(self):
        return self.name.lower()

    @staticmethod
    def from_string(s):
        try:
            return MarketSide[s.upper()]
        except KeyError:
            raise ValueError()

def check_best_trade(interval=Intervals.DAY):
    request_client = RequestClient(api_key=API_KEY, secret_key=SECRET_KEY)

    # Request info of all symbols to retrieve precision
    response = requests.get(BINANCE_FUTURES_BASE_URL + BINANCE_FUTURES_EXCHANGE_INFO_ENDPOINT)

    exchange_info = response.json()

    price_precision = 0
    best_bullish_wicks = []
    best_bearish_wicks = []
    print('Number of pairs to check approx: ', len(exchange_info['symbols']))
    for item in exchange_info['symbols']:
        if (not item['contractType'] == 'PERPETUAL'):
            continue
        print('\t * Checking: {}'.format(item['symbol']))
        candles = get_last_binance_candles(item['symbol'], interval, Markets.FUTURES)
        if (not len(candles) > 1):
            continue
        current_candle = candles[1]
        cc_open = float(current_candle[1])
        cc_high = float(current_candle[2])
        cc_low = float(current_candle[3])
        cc_close = float(current_candle[4])

        # Candle is green
        if (cc_open < cc_close):
            diff = cc_high - cc_close
            cc_wick = round((diff / cc_close) * 100, 2)
            best_bullish_wicks.append({ 'wick': cc_wick, 'symbol': item['symbol'] })
        else: # Candle is red
            diff = cc_low - cc_close
            cc_wick = -round((diff / cc_close) * 100, 2)
            best_bearish_wicks.append({ 'wick': cc_wick, 'symbol': item['symbol'] })
            

    bullish_result = sorted(best_bullish_wicks, key=lambda k: k['wick'], reverse=True)
    bearish_result = sorted(best_bearish_wicks, key=lambda k: k['wick'], reverse=False)
    
    print(white.bold('Best bullish wicks to trade found are:'))
    for item in bullish_result[0:10]:
        print(green.bold('\t{} -> {} % wick.'.format(item['symbol'], item['wick'])))

    print(white.bold('Best bearish wicks to trade found are:'))
    for item in bearish_result[0:10]:
        print(red.bold('\t{} -> {} % wick.'.format(item['symbol'], item['wick'])))

def check_open_trade_ready():
    global INITIAL_DELAY
    now = datetime.utcnow()
    hour_check = now.hour >= START_INTERVAL and now.hour <= END_INTERVAL
    if (hour_check):
        print(yellow("\nChecking candle open: {} -> {}.".format(now.strftime('%B %d %Y - %H:%M:%S'), hour_check)))
        if (not INITIAL_DELAY):
            print('Initial {} timeout'.format(SLEEP_TIMEOUT))
            time.sleep(SLEEP_TIMEOUT)
            INITIAL_DELAY = True
    else:
        print(yellow("\nChecking candle open: {} -> {}. Checking again in {} seconds.".format(now.strftime('%B %d %Y - %H:%M:%S'), hour_check, SLEEP_TIMEOUT)))
    return hour_check

def open_position_binance_futures(pair, take_profit, stop_loss, pair_change, quantity, leverage, side):
    global STOP_LOSS
    global TARGET
    global STOP_LOSS_REACHED
    global STOP_LOSS_ORDER_ID
    global TAKE_PROFIT_ORDER_ID

    request_client = RequestClient(api_key=API_KEY, secret_key=SECRET_KEY)
    # Cancel previous take profit and stop loss orders
    try:
        if (TAKE_PROFIT_ORDER_ID):
            request_client.cancel_order(symbol=pair, orderId=TAKE_PROFIT_ORDER_ID)
    except:
        print(red.bold('Take profit order id {} could not be cancelled'.format(TAKE_PROFIT_ORDER_ID)))

    try:
        if (STOP_LOSS_ORDER_ID):
            request_client.cancel_order(symbol=pair, orderId=STOP_LOSS_ORDER_ID)
    except:
        print(red.bold('Stop loss profit order id {} could not be cancelled'.format(STOP_LOSS_ORDER_ID)))

    STOP_LOSS_ORDER_ID = None
    TAKE_PROFIT_ORDER_ID = None
    # Change leverage
    try:
        request_client.change_initial_leverage(pair, leverage)
    except:
        print(red.bold('error changing leverage'))

    try:
        margin_type = request_client.change_margin_type(symbol=pair, marginType=FuturesMarginType.ISOLATED)
    except:
        print(red.bold('error changing margin type'))
    
    # Request info of all symbols to retrieve precision
    exchange_info = request_client.get_exchange_information()
    price_precision = 0
    for item in exchange_info.symbols:
        if (item.symbol == pair):
            precision = item.quantityPrecision
            price_precision = item.pricePrecision

    # Create order
    quantity_rounded = float(quantity * leverage) / float(pair_change)
    quantity_with_precision = "{:0.0{}f}".format(quantity_rounded, precision)
    
    stop_loss = "{:0.0{}f}".format(stop_loss, price_precision)
    take_profit = "{:0.0{}f}".format(take_profit, price_precision)

    STOP_LOSS = stop_loss
    STOP_LOSS_REACHED = False

    TARGET = take_profit

    print(white.bold('\n\tOpening future position {} at market ({}) with quantity: {} {} with take profit on: {} and stop loss: {}'.format(side, pair_change, quantity_with_precision, pair, take_profit, stop_loss)))
    order_side = OrderSide.BUY
    if (side == MarketSide.SHORT):
        order_side = OrderSide.SELL

    result = request_client.post_order(symbol=pair, side=order_side, quantity=quantity_with_precision, ordertype=OrderType.MARKET, positionSide="BOTH")
    orderId = result.orderId
    print(green.bold('\n\t\t✓ Market order created.'))

    # Set take profit and stop loss orders
    try:
        order_side = OrderSide.SELL
        if (side == MarketSide.SHORT):
            order_side = OrderSide.BUY
        result = request_client.post_order(symbol=pair, side=order_side, stopPrice=stop_loss, closePosition=True, ordertype=OrderType.STOP_MARKET, positionSide="BOTH", timeInForce="GTC")
        STOP_LOSS_ORDER_ID = result.orderId
        print(green.bold('\n\t\t✓ Stop market order at: {} created.'.format(stop_loss)))
        result = request_client.post_order(symbol=pair, side=order_side, stopPrice=take_profit, closePosition=True, ordertype=OrderType.TAKE_PROFIT_MARKET, positionSide="BOTH", timeInForce="GTC")
        TAKE_PROFIT_ORDER_ID = result.orderId
        print(green.bold('\n\t\t✓ Take profit market at: {} creted.'.format(take_profit)))
    except:
        # Cancel order if something did not work as expected
        request_client.cancel_order(symbol=pair, orderId=orderId)
        print(red.bold('\n\t\t x Something did not work as expected. Cancelling order'))

def open_position_binance_spot(pair, limit, pair_change, quantity, side = SpotSides.BUY):
    url = BINANCE_SPOT_BASE_URL + BINANCE_SPOT_CREATE_ORDER_ENDPOINT
    
    response = requests.get(BINANCE_SPOT_BASE_URL + BINANCE_SPOT_EXCHANGE_INFO_ENDPOINT)
    exchange_info = response.json()
    price_precision = 0
    for item in exchange_info["symbols"]:
        if (item["symbol"] == pair):
            price_precision = item["baseAssetPrecision"]

    quantity_rounded = float(quantity) / float(pair_change)
    quantity_with_precision = "{:0.0{}f}".format(quantity_rounded, price_precision)
    
    parameters = {}
    if (side == SpotSides.BUY):
        parameters = { "symbol": pair, "side": SpotSides.BUY, "type": "LIMIT", "price": limit, "quantity": quantity_with_precision }
    else:
        parameters = { "symbol": pair, "side": SpotSides.SELL, "type": "STOP_LOSS", "quantity": quantity_with_precision, "stopPrice": quantity_with_precision }
    
    response = requests.post(url, data = parameters)
    print(response)
    print('***********')
    print(response.json())

    print(white.bold('\n\tOpening spot position type LIMIT for {} pair limit {} with quantity: {}.'.format(pair, limit, quantity)))
    print(green.bold('\n\t\t✓ Limit order created at price: {}.'.format(limit)))


def fib_retracement(min, max):
    diff = max - min
    return { 1: min + 0.236 * diff, 2: min + 0.382 * diff, 3: min + 0.5 * diff, 4: min + 0.618 * diff}


def get_last_binance_candles(pair, interval, market=Markets.FUTURES):
    response = None
    limit = 2
    """if (interval == Intervals.TWO_WEEKS.value):
        two_week_reference = datetime.utcfromtimestamp(1618185600)
        now = datetime.utcfromtimestamp(1619433046)
        now = datetime.utcnow()
        diff = now - two_week_reference
        diff_in_minutes = (diff.total_seconds() % (14 * 24 * 60 * 60)) / 60
        diff_in_hours = diff_in_minutes / 60

        next_two_week_candle = (14 * 24) - diff_in_hours
        interval = Intervals.WEEK.value
        limit = 3
        if (next_two_week_candle < 24):
            limit = 4"""

    if (market == Markets.SPOT):
        url = '{}{}?symbol={}&interval={}&limit={}'.format(BINANCE_SPOT_BASE_URL, BINANCE_SPOT_KLINES_ENDPOINT, pair, interval, limit)
        response = requests.get(url)
    else:
        response = requests.get('{}{}?pair={}&interval={}&limit={}&contractType=PERPETUAL'.format(BINANCE_FUTURES_BASE_URL, BINANCE_FUTURES_KLINES_ENDPOINT, pair, interval, limit))
    data = response.json()

    result = data
    # Parse intervals non accepted by binance API (2w)
    if (len(result) > 2):
        first_week = result[0]
        second_week = result[1]
        third_week = result[2]
        lc_low = min(float(first_week[3]), float(second_week[3]))
        lc_open = float(first_week[1])
        lc_close = float(second_week[4])
        lc_high = max(float(first_week[2]), float(second_week[2]))

        cc_low = float(third_week[3])
        cc_open = float(third_week[1])
        cc_close = float(third_week[4])
        cc_high = float(third_week[2])

        if (next_two_week_candle < 24):
            fourth_week = result[3]
            cc_low = min(float(third_week[3]), float(fourth_week[3]))
            cc_open = float(third_week[1])
            cc_close = float(fourth_week[4])
            cc_high = max(float(third_week[2]), float(fourth_week[2]))
        result = [[first_week[0], lc_open, lc_high, lc_low, lc_close], [third_week[0], cc_open, cc_high, cc_low, cc_close]]  

    return result

def check_safe_stop_loss(low, open):
    diff = open - low
    trade_risk = (diff / low) * 100
    is_safe = MAX_STOP_LOSS_RISK > trade_risk
    print(yellow.bold('\n\t⚠ Position risk is: {}%'.format(round(trade_risk, 2))))
    if not is_safe:
        print(red.bold('\n\tTrade is too risky ({}), aborting!.'.format(trade_risk)))
        exit(1)
    return is_safe

def set_sleep_timeout(interval):
    global SLEEP_TIMEOUT
    sleep = 15
    low_tf_sleep = 3
    """if (interval == Intervals.FIVETEEN_MINUTES.value):
        sleep = low_tf_sleep
    elif (interval == Intervals.THIRTY_MINUTES.value):
        sleep = low_tf_sleep"""
    if (interval == Intervals.HOUR.value):
        sleep = low_tf_sleep
    elif (interval == Intervals.FOUR_HOURS.value):
        sleep = low_tf_sleep
    elif (interval == Intervals.TWELVE_HOURS.value):
        sleep = low_tf_sleep
    SLEEP_TIMEOUT = sleep

def minimum_downside(cc_open, cc_low):
    diff = cc_open - cc_low
    downside = (diff / cc_low) * 100
    return downside > 0.5

def trade_the_open(pair, interval, quantity, leverage, market, side, limit, target):
    global LAST_CANDLE_RED
    global LAST_CANDLE_GREEN
    global LAST_LOW_PRICE
    global LAST_HIGH_PRICE
    global TIMES_GREEN
    global TIMES_RED
    global TARGET
    global TARGET_REACHED
    global STOP_LOSS_REACHED
    global STOP_LOSS

    try:
        candles = get_last_binance_candles(pair, interval, market)
    except:
        time.sleep(SLEEP_TIMEOUT)
        candles = get_last_binance_candles(pair, interval, market)
            
    """ Binance API response format
    [
        [
            1499040000000,      // Open time
            "0.01634790",       // Open
            "0.80000000",       // High
            "0.01575800",       // Low
            "0.01577100",       // Close
            "148976.11427815",  // Volume
            1499644799999,      // Close time
            "2434.19055334",    // Quote asset volume
            308,                // Number of trades
            "1756.87402397",    // Taker buy base asset volume
            "28.46694368",      // Taker buy quote asset volume
            "17928899.62484339" // Ignore.
        ]
    ]"""

    last_candle = candles[0]
    lc_open = float(last_candle[1])
    lc_high = float(last_candle[2])
    lc_low = float(last_candle[3])
    lc_close = float(last_candle[4])

    current_candle = candles[1]
    cc_open = float(current_candle[1])
    cc_high = float(current_candle[2])
    cc_low = float(current_candle[3])
    cc_close = float(current_candle[4])
    # Check if candlestick turned green

    if (side == MarketSide.LONG):
        if (cc_high >= float(TARGET)):
            TARGET_REACHED = True
    else:
        if (cc_low <= float(TARGET)):
            TARGET_REACHED = True

    if (cc_low <= float(STOP_LOSS)):
        STOP_LOSS_REACHED = True

    if (TIMES_GREEN > 1 and not STOP_LOSS_REACHED):
        return False

    # LONG trades
    if (side == MarketSide.LONG):
        if (cc_open < cc_close and cc_open >= cc_low):
            if (LAST_CANDLE_RED and cc_low < LAST_LOW_PRICE):
                print('***** INTENTO NUMERO: {} ******'.format(TIMES_GREEN))
                TIMES_GREEN += 1
                LAST_CANDLE_RED = False
                LAST_LOW_PRICE = cc_low
            else:
                print(' x - Todavia esta verde como para volver a intentarlo, target reached?: ', TARGET_REACHED, TARGET)
                return False
            print(green.bold('\n\tCandle turned green.'))
            # Check if previous candle is green or red to apply fib retracement
            if (lc_open < lc_close):
                # Previous candle is green
                targets = fib_retracement(lc_close, lc_high)
            else:
                # Previous candle is red
                targets = fib_retracement(lc_open, lc_high)
            print(white.bold('\n\tTargets based on fib retracement: {}'.format(targets)))
            #if (not minimum_downside(cc_open, cc_low)):
                #return False

            if (check_safe_stop_loss(cc_low, cc_open)):
                if (market == Markets.FUTURES):
                    open_position_binance_futures(pair, targets[target], cc_low, cc_close, quantity, leverage, side)
                else:
                    open_position_binance_spot(pair, cc_close, cc_close, quantity, SpotSides.BUY)
                return True
        else:
            if not LAST_CANDLE_RED:
                LAST_CANDLE_RED = True
            print(yellow.bold('\t Candle is still RED after the open. Checking again in {} seconds'.format(SLEEP_TIMEOUT)))    
            return False
    
    else: #SHORT TRADES!
        if (cc_open > cc_close and cc_open <= cc_high):
            print(LAST_CANDLE_GREEN, cc_high, ' < ', LAST_HIGH_PRICE)
            if (LAST_CANDLE_GREEN and cc_high > LAST_HIGH_PRICE):
                print('***** INTENTO NUMERO: {} ******'.format(TIMES_RED))
                TIMES_RED += 1
                LAST_CANDLE_GREEN = False
                LAST_HIGH_PRICE = cc_high
            else:
                print(' x - Candle still red to try again. Target reached?: ', TARGET_REACHED, TARGET)
                return False
            print(green.bold('\n\tCandle turned red.'))
            # Check if previous candle is red to apply fib retracement
            if (lc_open < lc_close):
                # Previous candle is green
                targets = fib_retracement(lc_open, lc_low)
            else:
                # Previous candle is red
                targets = fib_retracement(lc_close, lc_low)
            print(white.bold('\n\tTargets based on fib retracement: {}'.format(targets)))

            print('COMPROBANDO SAFE SL')
            if (check_safe_stop_loss(cc_open, cc_high)):
                if (market == Markets.FUTURES):
                    print('ABRO SHORT')
                    open_position_binance_futures(pair, targets[target], cc_high, cc_close, quantity, leverage, side)
                else:
                    open_position_binance_spot(pair, cc_close, cc_close, quantity, SpotSides.BUY)
                return True
        else:
            if not LAST_CANDLE_GREEN:
                LAST_CANDLE_GREEN = True
            print(yellow.bold('\t Candle is still GREEN after the open. Checking again in {} seconds'.format(SLEEP_TIMEOUT)))    
            return False
def main(pair, quantity, interval=Intervals.DAY, leverage=2, market=Markets.FUTURES, side=MarketSide.LONG, limit=0, target=1):
    order_filled = False
    global TARGET 
    set_sleep_timeout(interval)
    if (side == MarketSide.SHORT):
        TARGET = 0

    print(white.bold('* Liquidity trading of: {} with {} as amount at {} candle with x{} leverage and at {} market starting at {} and finishing at {}.'.format(pair, quantity, interval, leverage, market, START_INTERVAL, END_INTERVAL)))
    while not TARGET_REACHED and (not order_filled or TIMES_GREEN < MAX_ORDER_RETRIES):
        if (check_open_trade_ready()):
            order_filled = trade_the_open(pair, interval, quantity, leverage, market, side, limit, target)
        if (not order_filled):
            time.sleep(SLEEP_TIMEOUT)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Trade the open of candles in different timeframes.')
    parser.add_argument('--pair', type=str, help='Cryptocurrency pair to trade.')
    parser.add_argument('--quantity', type=float, help='Quantity in USD to trade.')
    parser.add_argument('--interval', type=Intervals.from_string, choices=list(Intervals), help='Candle timeframe to trade.')
    parser.add_argument('--leverage', type=int, help='Leverage to apply on the trade.')
    parser.add_argument('--market', type=Markets.from_string, help='Market where the will be executed.', default=Markets.FUTURES)
    parser.add_argument('--side', type=MarketSide.from_string, help='Type of order to be executed.', default=MarketSide.LONG)
    parser.add_argument('--limit', type=float, help='Limit for spot orders.')
    parser.add_argument('--start', type=int, help='Candle UTC start.', default=0)
    parser.add_argument('--end', type=int, help='Candle UTC end.', default=8)
    parser.add_argument('--risk', type=int, help='Risk to take with the trade.', default=4)
    parser.add_argument('--target', type=int, help='Fibonnacci target to reach.', default=4)
    parser.add_argument('--check', action='store_true', help='Check best pair to trade.')

    args = parser.parse_args()

    if (args.check):
        check_best_trade(args.interval.value)
        sys.exit()

    if (args.market == Markets.FUTURES):
        args.pair = args.pair + 'USDT'

    START_INTERVAL = args.start
    END_INTERVAL = args.end

    MAX_STOP_LOSS_RISK = args.risk
    main(args.pair, args.quantity, args.interval.value, args.leverage, args.market, args.side, args.limit, args.target)

    print(green.bold('\nOrders successfully set.'))
