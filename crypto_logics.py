import ccxt
import pandas as pd
import logging
# Bithumb V2 Imports
import jwt # PyJWT
import uuid
import time
import hashlib
import urllib.parse
import requests
import json

class CryptoLogic:
    def __init__(self, exchange_id, api_key, secret_key, password=None, market_type='spot'):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.secret_key = secret_key
        self.password = password
        self.market_type = market_type
        self.exchange = None
        self.logger = logging.getLogger("TradingBot")
        
        self.connect()

    def connect(self):
        try:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'enableRateLimit': True,
                'options': {'defaultType': self.market_type} 
            }
            if self.password:
                config['password'] = self.password
            
            # Special handling for Bithumb V2 keys in CCXT?
            # CCXT doesn't fully support Bithumb V2 JWT automatically yet with just these keys if they are 48/84 length.
            # We initialize CCXT anyway for Public API (Ticker).
            # For Bithumb, public API doesn't need keys, so valid or invalid keys here might be fine for public calls,
            # but private calls will fail in CCXT. We will handle private calls manually.
            
            self.exchange = exchange_class(config)
            
            type_str = "Spot"
            if self.market_type == 'future':
                type_str = "Futures (USD-M)" 
            elif self.market_type == 'delivery':
                type_str = "Futures (COIN-M)"
            
            self.logger.info(f"{self.exchange_id} Connected. Mode: {type_str}")
        except Exception as e:
            self.logger.error(f"Failed to connect to {self.exchange_id}: {e}")
            self.exchange = None

    # --- Bithumb V2 Helper ---
    def _get_bithumb_jwt_header(self, query_params=None):
        """
        Generates JWT Authorization header for Bithumb API 2.0
        """
        nonce = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)
        
        q_str = ""
        if query_params:
            q_str = urllib.parse.urlencode(query_params)
        
        query_hash = hashlib.sha512(q_str.encode('utf-8')).hexdigest()
        
        payload = {
            'access_key': self.api_key.strip(),
            'nonce': nonce,
            'timestamp': timestamp,
            'query_hash': query_hash,
            'query_hash_alg': 'SHA512'
        }
        
        # Sign
        token = jwt.encode(payload, self.secret_key.strip(), algorithm='HS512')
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

    def fetch_balance(self):
        """
        Returns a DataFrame with columns: ['Symbol', 'Free', 'Used', 'Total']
        """
        # --- Bithumb V2 Override ---
        if self.exchange_id == 'bithumb':
            return self._fetch_balance_bithumb_v2()

        if not self.exchange: return pd.DataFrame()

        try:
            balance = self.exchange.fetch_balance()
            data = []
            
            # Different exchanges have different balance structures, but ccxt normalizes most
            items = balance['total'].items()
            
            for currency, amount in items:
                if amount > 0:
                    free = balance[currency].get('free', 0)
                    used = balance[currency].get('used', 0)
                    avg_price = 0.0
                    
                    # Upbit specific extraction
                    if self.exchange_id == 'upbit':
                         if isinstance(balance.get('info'), list):
                             for asset in balance['info']:
                                 if asset.get('currency') == currency:
                                     avg_price = float(asset.get('avg_buy_price', 0))
                                     break
                    
                    data.append({
                        'Symbol': currency, 
                        'Free': free,
                        'Used': used,
                        'Total': amount,
                        'AvgPrice': avg_price
                    })
            
            return pd.DataFrame(data)
        except Exception as e:
            self.logger.error(f"Error fetching balance for {self.exchange_id}: {e}")
            return pd.DataFrame()

    def _fetch_balance_bithumb_v2(self):
        """
        Bithumb API 2.0 Balance Implementation
        Endpoint: /v1/accounts
        """
        try:
            url = "https://api.bithumb.com/v1/accounts"
            headers = self._get_bithumb_jwt_header()
            
            res = requests.get(url, headers=headers, timeout=5)
            
            if res.status_code != 200:
                self.logger.error(f"Bithumb V2 Balance Error: {res.status_code} {res.text}")
                return pd.DataFrame()
            
            # Response: [{"currency":"KRW","balance":"...","locked":"...","avg_buy_price":"..."}, ...]
            items = res.json()
            data = []
            
            for item in items:
                currency = item['currency']
                # Bithumb V2 returns all supported coins, many with 0 balance. Filter > 0.
                balance_free = float(item.get('balance', 0))
                balance_locked = float(item.get('locked', 0))
                total = balance_free + balance_locked
                
                if total > 0.00000001: # Filter small dust
                    avg = float(item.get('avg_buy_price', 0))
                    
                    data.append({
                        'Symbol': currency,
                        'Free': balance_free,
                        'Used': balance_locked,
                        'Total': total,
                        'AvgPrice': avg
                    })
            
            return pd.DataFrame(data)

        except Exception as e:
            self.logger.error(f"Bithumb V2 Balance Exception: {e}")
            return pd.DataFrame()

    def fetch_positions(self):
        """
        For Futures: Returns DataFrame of Open Positions.
        """
        if not self.exchange: return pd.DataFrame()
        
        try:
            positions = self.exchange.fetch_positions()
            data = []
            for pos in positions:
                amt = float(pos.get('contracts', 0) or pos.get('info', {}).get('positionAmt', 0))
                if amt == 0: continue
                
                symbol = pos['symbol']
                entry = float(pos.get('entryPrice', 0))
                
                data.append({
                    'Symbol': symbol,
                    'Free': 0,
                    'Used': 0,
                    'Total': abs(amt), 
                    'AvgPrice': entry
                })
            return pd.DataFrame(data)
        except Exception as e:
            # self.logger.error(f"Error fetching positions for {self.exchange_id}: {e}")
            # Silence this error if market type isn't supported or if it's spot trying to fetch pos
            return pd.DataFrame()

    def fetch_ticker(self, symbol):
        if not self.exchange: return None
        try:
            # CCXT Public API usually works fine
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            self.logger.error(f"Error fetching ticker for {symbol} on {self.exchange_id}: {e}")
            return None

    def create_market_sell_order(self, symbol, amount):
        # --- Bithumb V2 Override ---
        if self.exchange_id == 'bithumb':
            return self._create_order_bithumb_v2(symbol, 'ask', amount)

        if not self.exchange: return None

        try:
            order = self.exchange.create_market_sell_order(symbol, amount)
            self.logger.info(f"SOLD {amount} of {symbol} on {self.exchange_id}")
            return order
        except Exception as e:
            self.logger.error(f"Failed to sell {symbol} on {self.exchange_id}: {e}")
            return None

    def _create_order_bithumb_v2(self, symbol, side, amount):
        """
        Bithumb API 2.0 Order Implementation
        Endpoint: POST /v2/orders
        """
        try:
            url = "https://api.bithumb.com/v2/orders"
            
            # Symbol format: CCXT uses 'BTC/KRW', Bithumb V2 uses 'KRW-BTC' usually?
            # Let's check typical responses.
            # v1 accounts returns "KRW" or "BTC".
            # v2 orders requires 'market'.
            # Upbit uses 'KRW-BTC'. Bithumb V2 likely follows standard 'KRW-BTC' format.
            # However, logic helper 'get_market_symbol' usually returns 'BTC/KRW' for ccxt.
            # We need to convert.
            
            # Input symbol: 'BTC/KRW' or 'BTC'
            if '/' in symbol:
                base, quote = symbol.split('/')
                market_id = f"{quote}-{base}" # e.g. KRW-BTC
            else:
                # Fallback guess
                market_id = f"KRW-{symbol}"

            # Params
            # ord_type: 'market' might fail if Bithumb only supports 'price' for market buy or 'volume' for market sell?
            # API Docs:
            # market: KRW-BTC
            # side: ask/bid
            # volume: quantity (for limit/market-ask)
            # price: price (for limit)
            # ord_type: limit, price(market buy), market(market sell?) => check docs carefully or try 'market'
            
            # Common V2 pattern: ord_type='market'
            
            # 2. Fetch Current Price to Simulate Market Order
            # We want to sell IMMEDIATELY, so we set price slightly lower (Ask)
            ticker = self.exchange.fetch_ticker(symbol)
            curr_price = ticker['last']
            
            # Sell Price: 95% of current price to ensure fill (Market Sell behavior)
            # Bithumb limits order price range, but -5% is usually safe for immediate fill
            target_price = curr_price * 0.95
            
            # 3. Precision Formatting (Crucial for Bithumb)
            # Use CCXT's loaded market data for precision
            price_str = self.exchange.price_to_precision(symbol, target_price)
            volume_str = self.exchange.amount_to_precision(symbol, amount)

            # 4. Construct Params (Limit Order)
            params = {
                'market': market_id,
                'side': side, # 'ask'
                'volume': volume_str,
                'price': price_str,
                'order_type': 'limit' 
            }
            
            # Request Body JSON
            json_body = json.dumps(params)
             
            # Sign
            # TRY 3: Use urlencode for query_hash even for POST.
            # Many Connect API implementations unify GET/POST signing this way.
            # Note: We must ensure the params order/content matches exactly what server expects?
            # urlencode normally sorts? No. We might need sorting.
            # Let's try simple urlencode first.
            q_str = urllib.parse.urlencode(params)
            query_hash = hashlib.sha512(q_str.encode('utf-8')).hexdigest()
            
            nonce = str(uuid.uuid4())
            timestamp = int(time.time() * 1000)


            
            payload = {
                'access_key': self.api_key.strip(),
                'nonce': nonce,
                'timestamp': timestamp,
                'query_hash': query_hash,
                'query_hash_alg': 'SHA512'
            }
            token = jwt.encode(payload, self.secret_key.strip(), algorithm='HS512')
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
            
            res = requests.post(url, headers=headers, data=json_body, timeout=5)
            
            if res.status_code in [200, 201]:
                self.logger.info(f"Bithumb V2 SOLD {amount} of {market_id}")
                return res.json()
            else:
                self.logger.error(f"Bithumb V2 Sell Failed: {res.status_code} {res.text}")
                return None

        except Exception as e:
            self.logger.error(f"Bithumb V2 Order Exception: {e}")
            return None

    def get_market_symbol(self, coin):
        """
        Helper to guess the market pair.
        """
        if self.exchange_id in ['upbit', 'bithumb']:
            return f"{coin}/KRW"
        else:
            return f"{coin}/USDT"

