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

    @staticmethod
    def _to_float(value, default=0.0):
        """Convert CCXT's optional numeric fields without leaking parser errors."""
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def fetch_balance(self, account_type=None):
        """
        Returns a DataFrame with columns: ['Symbol', 'Free', 'Used', 'Total']

        ``account_type`` is passed through to CCXT as ``params['type']``.  Binance
        accepts values such as ``spot`` and ``future``; OKX accepts ``trading``.
        """
        # --- Bithumb V2 Override ---
        if self.exchange_id == 'bithumb':
            return self._fetch_balance_bithumb_v2()

        if not self.exchange: return pd.DataFrame()

        try:
            params = {'type': account_type} if account_type else {}
            balance = self.exchange.fetch_balance(params)
            data = []
            
            # Different exchanges have different balance structures, but ccxt normalizes most
            items = balance['total'].items()
            
            for currency, amount in items:
                total = self._to_float(amount)
                if total > 0:
                    asset_balance = balance.get(currency) or {}
                    free = self._to_float(asset_balance.get('free', 0))
                    used = self._to_float(asset_balance.get('used', 0))
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
                        'Total': total,
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
        Return open USDT-settled linear futures and swap positions.

        The result intentionally excludes options and coin-margined contracts.  It
        retains the normalized CCXT fields needed to display and safely close a
        position instead of treating contract quantity as a spot-asset balance.
        """
        if not self.exchange: return pd.DataFrame()
        
        try:
            positions = self.exchange.fetch_positions()
            data = []
            for pos in positions:
                info = pos.get('info') or {}
                raw_contracts = pos.get('contracts')
                if raw_contracts in (None, 0, 0.0):
                    raw_contracts = info.get('positionAmt', info.get('pos', 0))
                signed_contracts = self._to_float(raw_contracts)
                contracts = abs(signed_contracts)
                if contracts == 0:
                    continue

                settle = str(
                    pos.get('settle')
                    or info.get('settleCcy')
                    or info.get('marginAsset')
                    or ''
                ).upper()
                position_type = str(pos.get('type') or info.get('instType') or '').lower()
                linear = pos.get('linear')

                # CCXT exposes a normalized ``linear`` flag for supported exchanges.
                # The settlement fallback keeps older CCXT responses compatible.
                if settle != 'USDT' or linear is False or position_type not in ('swap', 'future'):
                    continue

                symbol = pos.get('symbol')
                if not symbol:
                    continue
                side = str(pos.get('side') or '').lower()
                if side not in ('long', 'short'):
                    side = 'short' if signed_contracts < 0 else 'long'

                entry = self._to_float(pos.get('entryPrice', info.get('avgPx', info.get('entryPrice', 0))))
                mark = self._to_float(pos.get('markPrice', info.get('markPx', 0)))
                contract_size = self._to_float(pos.get('contractSize', 1), 1.0)
                notional = abs(self._to_float(pos.get('notional', info.get('notionalUsd', 0))))
                if notional == 0 and mark > 0:
                    notional = contracts * contract_size * mark
                unrealized_pnl = self._to_float(
                    pos.get('unrealizedPnl', info.get('upl', info.get('unrealizedProfit', 0)))
                )
                
                data.append({
                    'Symbol': symbol,
                    'Free': 0,
                    'Used': 0,
                    'Total': contracts,
                    'AvgPrice': entry,
                    'MarkPrice': mark,
                    'Notional': notional,
                    'UnrealizedPnl': unrealized_pnl,
                    'ContractSize': contract_size,
                    'Side': side,
                    'Hedged': bool(pos.get('hedged', False)),
                    'Settle': settle,
                    'PositionType': position_type,
                })
            return pd.DataFrame(data)
        except Exception as e:
            self.logger.error(f"Error fetching positions for {self.exchange_id}: {e}")
            return pd.DataFrame()

    def close_futures_position(self, symbol, amount, side, hedged=False):
        """Close an existing futures position without allowing a reversal/opening order."""
        if not self.exchange:
            return None

        position_side = str(side or '').lower()
        contracts = self._to_float(amount)
        if position_side not in ('long', 'short') or contracts <= 0:
            self.logger.error(
                f"Invalid futures close request on {self.exchange_id}: "
                f"side={side}, amount={amount}"
            )
            return None

        close_side = 'sell' if position_side == 'long' else 'buy'
        params = {'reduceOnly': True}
        # CCXT translates ``hedged`` to Binance positionSide / OKX posSide while
        # preserving the opposite order side required to close the position.
        if hedged:
            params['hedged'] = True

        try:
            order = self.exchange.create_order(
                symbol, 'market', close_side, contracts, None, params
            )
            self.logger.info(
                f"CLOSED {position_side.upper()} {contracts} of {symbol} on {self.exchange_id}"
            )
            return order
        except Exception as e:
            self.logger.error(f"Failed to close futures position on {self.exchange_id}: {e}")
            return None

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

