from nicegui import ui, app
import pandas as pd
import time
import logging
from datetime import datetime
import asyncio

# Local imports
import config_manager
from crypto_logics import CryptoLogic

# --- CONFIG & STATE ---
config = config_manager.load_config()

# Migration helpers (same as original)
migrated = False
for ex in ["binance", "okx", "bithumb", "upbit"]:
    if 'market_type' not in config['exchanges'][ex]:
        config['exchanges'][ex]['market_type'] = 'spot'
        migrated = True
    if 'sl_enabled' not in config['exchanges'][ex]:
        config['exchanges'][ex]['sl_enabled'] = True
        migrated = True
    if 'tp_enabled' not in config['exchanges'][ex]:
        config['exchanges'][ex]['tp_enabled'] = True
        migrated = True
if 'global_settings' not in config:
    config['global_settings'] = {'auto_trading': False, 'update_interval': 5}
    migrated = True
elif 'update_interval' not in config['global_settings']:
    config['global_settings']['update_interval'] = 5
    migrated = True
if migrated:
    config_manager.save_config(config)

# --- LOGGING SETUP ---
class NiceGuiHandler(logging.Handler):
    def __init__(self, log_element):
        super().__init__()
        self.log_element = log_element

    def emit(self, record):
        try:
            msg = self.format(record)
            ts = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{ts}] {msg}"
            self.log_element.push(log_entry)
        except Exception:
            pass

class TpslApp:
    def __init__(self):
        self.running = config['global_settings']['auto_trading']
        self.hidden_coins = set()
        self.logger = logging.getLogger("TradingBot")
        self.clients = {}
        self.connected = {ex: False for ex in ["binance", "okx", "bithumb", "upbit"]}
        
        # UI Elements
        self.log_area = None
        self.table_container = None
        self.metrics_container = None
        self.history_container = None
        
        # UI Setup moved to @ui.page('/') handler
        # self.setup_ui()
        # self.setup_logger()
        
        # Start Update Loop
        # self.update_timer = ui.timer(config['global_settings']['update_interval'], self.update_loop)
        # Using asyncio task instead of ui.timer to avoid global UI scope issues

        # Transaction History
        self.trade_history = []

    def setup_logger(self):
        self.logger = logging.getLogger("TradingBot")
        self.logger.setLevel(logging.INFO)
        
        # Clear existing handlers to avoid duplicates on reload
        self.logger.handlers = []
        
        # File Handler
        try:
            file_handler = logging.FileHandler("trading_log.txt", encoding='utf-8')
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except:
            pass
            
        # NiceGUI Handler
        if self.log_area:
            ng_handler = NiceGuiHandler(self.log_area)
            formatter = logging.Formatter('%(message)s')
            ng_handler.setFormatter(formatter)
            self.logger.addHandler(ng_handler)

    def text_log(self, msg):
        self.logger.info(msg)

    def save_config(self):
        config_manager.save_config(config)

    def connect_exchange(self, ex):
        creds = config['exchanges'][ex]
        # Binance and OKX are queried in parallel for spot and USDT futures.
        # The old market_type setting is kept only for configuration compatibility.
        client_specs = [('spot', 'spot')]
        if ex == 'binance':
            client_specs.append(('future', 'future'))
        elif ex == 'okx':
            client_specs.append(('future', 'swap'))

        clients = {}
        for account_scope, market_type in client_specs:
            try:
                client = CryptoLogic(
                    ex,
                    creds['api_key'],
                    creds['secret_key'],
                    creds.get('password'),
                    market_type,
                )
                if client.exchange:
                    clients[account_scope] = client
            except Exception as e:
                self.text_log(f"Connection Error {ex} ({account_scope}): {e}")

        self.clients[ex] = clients
        self.connected[ex] = bool(clients)
        if clients:
            scopes = ', '.join(clients.keys())
            self.text_log(f"Connected {ex} ({scopes})")
            ui.notify(f"Connected {ex}: {scopes}", type='positive')
        else:
            ui.notify(f"Failed to connect {ex}", type='negative')
            
    def disconnect_exchange(self, ex):
        self.clients.pop(ex, None)
        self.connected[ex] = False
        self.text_log(f"Disconnected {ex}")
        ui.notify(f"Disconnected {ex}", type='info')

    async def run_background_loop(self):
        while True:
            try:
                await self.update_loop()
            except Exception as e:
                print(f"Background Loop Error: {e}") # Fallback logging
            
            # Dynamic sleep based on current config
            interval = config['global_settings'].get('update_interval', 5)
            await asyncio.sleep(interval)

    async def update_loop(self):
        if not any(self.connected.values()):
            return

        try:
            usdt_krw = 1400.0
            try:
                # Simple cache logic or fetch every time? 
                # Let's fetch every loop for now, or use a separate timer for rate.
                # For simplicity, fetch here.
                import requests
                url = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"
                data = await asyncio.to_thread(requests.get, url)
                usdt_krw = float(data.json()[0]['trade_price'])
            except:
                pass

            total_krw = 0.0
            ex_sums = {x: 0.0 for x in ["binance", "okx", "bithumb", "upbit"]}
            rows = []

            for ex, exchange_clients in list(self.clients.items()):
                if not exchange_clients:
                    continue

                # A Binance futures wallet is distinct from the spot wallet.  OKX
                # uses a unified trading wallet, so it is fetched once to avoid
                # counting the same collateral twice.
                if ex == 'binance':
                    balance_specs = [
                        ('spot', 'spot', '현물'),
                        ('future', 'future', '선물 증거금'),
                    ]
                elif ex == 'okx':
                    balance_specs = [('spot', 'trading', 'OKX 거래계정')]
                else:
                    balance_specs = [('spot', None, '현물')]

                for client_scope, account_type, account_label in balance_specs:
                    client = exchange_clients.get(client_scope)
                    if not client:
                        continue
                    balance_rows, balance_total = await self._build_balance_rows(
                        ex, client, client_scope, account_type, account_label, usdt_krw
                    )
                    rows.extend(balance_rows)
                    ex_sums[ex] += balance_total
                    total_krw += balance_total

                if ex in ('binance', 'okx'):
                    future_client = exchange_clients.get('future')
                    if future_client:
                        rows.extend(
                            await self._build_futures_rows(ex, future_client, usdt_krw)
                        )

            # Update UI
            self.update_dashboard(rows, total_krw, ex_sums)

        except RuntimeError as e:
            if "client" in str(e) and "deleted" in str(e):
                pass # Ignore client deletion errors during reload
            else:
                self.text_log(f"Loop Runtime Error: {e}")
        except Exception as e:
            self.text_log(f"Loop Error: {e}")

    @staticmethod
    def _as_float(value, default=0.0):
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_cash_symbol(symbol, is_domestic):
        if is_domestic:
            return symbol == 'KRW'
        return symbol in {'USDT', 'USDC', 'BUSD', 'DAI'}

    @staticmethod
    def _format_price(value):
        return f"{value:.4f}" if value > 0 else '-'

    def _is_whitelisted(self, ex, symbol):
        whitelist = config['exchanges'][ex].get('whitelist', [])
        if isinstance(whitelist, str):
            whitelist = [item.strip().upper() for item in whitelist.split(',') if item.strip()]
        if not whitelist:
            return True
        base_symbol = symbol.split('/')[0].upper()
        return symbol.upper() in whitelist or base_symbol in whitelist

    async def _build_balance_rows(self, ex, client, client_scope, account_type, account_label, usdt_krw):
        try:
            df = await asyncio.to_thread(client.fetch_balance, account_type)
        except Exception as e:
            self.text_log(f"Fetch Error {ex} {client_scope} balance: {e}")
            return [], 0.0

        if df.empty:
            return [], 0.0

        rows = []
        balance_total = 0.0
        is_domestic = ex in ('bithumb', 'upbit')
        for _, balance in df.iterrows():
            symbol = str(balance.get('Symbol', '')).upper()
            amount = self._as_float(balance.get('Total'))
            if not symbol or amount <= 0 or symbol in {'P', 'POINT', 'APENFT'}:
                continue

            is_cash = self._is_cash_symbol(symbol, is_domestic)
            market_symbol = None
            current_price = 0.0
            if is_cash:
                current_price = 1.0
            else:
                market_symbol = f"{symbol}/KRW" if is_domestic else f"{symbol}/USDT"
                try:
                    current_price = self._as_float(
                        await asyncio.to_thread(client.fetch_ticker, market_symbol)
                    )
                except Exception as e:
                    self.text_log(f"Price Error {ex} {symbol}: {e}")

            rate = 1.0 if is_domestic else usdt_krw
            value_krw = amount * current_price * rate
            # Keep cash rows visible; dust tokens remain suppressed when priced.
            if current_price > 0 and value_krw < 10000 and not is_cash:
                continue

            avg_price = self._as_float(balance.get('AvgPrice'))
            pnl_pct = 0.0
            pnl_krw = 0.0
            if avg_price > 0 and current_price > 0:
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
                pnl_krw = (current_price - avg_price) * amount * rate

            kind = account_label
            if client_scope == 'spot' and account_label == '현물' and is_cash:
                kind = '현물 현금'
            if client_scope == 'future' and is_cash:
                kind = '선물 증거금'

            row = {
                'ex': ex,
                'kind': kind,
                'sym': symbol,
                'amt': f"{amount:.4f}",
                'val': f"₩{value_krw:,.0f}" if current_price > 0 else '가격 조회 실패',
                'avg': self._format_price(avg_price),
                'cur': self._format_price(current_price),
                'pnl': f"{pnl_pct:.2f}%",
                'pnl_val': pnl_pct,
                'revenue': pnl_krw,
                'market': market_symbol,
                'raw_amt': amount,
                'client_scope': client_scope,
                'is_future': False,
                'side': None,
                'hedged': False,
                'action': 'sell' if client_scope == 'spot' and market_symbol else None,
            }
            row['status'] = await self._apply_tpsl(ex, client, row)
            rows.append(row)
            balance_total += value_krw

        return rows, balance_total

    async def _build_futures_rows(self, ex, client, usdt_krw):
        try:
            df = await asyncio.to_thread(client.fetch_positions)
        except Exception as e:
            self.text_log(f"Fetch Error {ex} futures positions: {e}")
            return []

        if df.empty:
            return []

        rows = []
        for _, position in df.iterrows():
            symbol = str(position.get('Symbol', ''))
            contracts = self._as_float(position.get('Total'))
            if not symbol or contracts <= 0:
                continue

            side = str(position.get('Side', 'long')).lower()
            if side not in ('long', 'short'):
                side = 'long'
            entry_price = self._as_float(position.get('AvgPrice'))
            mark_price = self._as_float(position.get('MarkPrice'))
            if mark_price <= 0:
                try:
                    mark_price = self._as_float(await asyncio.to_thread(client.fetch_ticker, symbol))
                except Exception as e:
                    self.text_log(f"Price Error {ex} {symbol}: {e}")

            notional = abs(self._as_float(position.get('Notional')))
            if notional <= 0 and mark_price > 0:
                notional = contracts * self._as_float(position.get('ContractSize'), 1.0) * mark_price

            pnl_pct = 0.0
            if entry_price > 0 and mark_price > 0:
                price_change_pct = ((mark_price - entry_price) / entry_price) * 100
                pnl_pct = price_change_pct if side == 'long' else -price_change_pct
            pnl_krw = self._as_float(position.get('UnrealizedPnl')) * usdt_krw

            row = {
                'ex': ex,
                'kind': f"선물 포지션 · {side.upper()}",
                'sym': symbol,
                'amt': f"{contracts:.4f}",
                'val': f"₩{notional * usdt_krw:,.0f}" if notional > 0 else '가격 조회 실패',
                'avg': self._format_price(entry_price),
                'cur': self._format_price(mark_price),
                'pnl': f"{pnl_pct:.2f}%",
                'pnl_val': pnl_pct,
                'revenue': pnl_krw,
                'market': symbol,
                'raw_amt': contracts,
                'client_scope': 'future',
                'is_future': True,
                'side': side,
                'hedged': bool(position.get('Hedged', False)),
                'action': 'close',
            }
            # Futures notional is display-only and is intentionally excluded from
            # total_krw/ex_sums to avoid counting leveraged exposure as cash.
            row['status'] = await self._apply_tpsl(ex, client, row)
            rows.append(row)

        return rows

    async def _apply_tpsl(self, ex, client, row):
        if not row.get('action'):
            return 'wallet'
        if not self.running:
            return 'off'
        if not self._is_whitelisted(ex, row['sym']):
            return 'wait'

        settings = config['exchanges'][ex]
        trigger = None
        if settings.get('sl_enabled', True) and row['pnl_val'] <= -self._as_float(settings.get('sl')):
            trigger = 'SL'
        elif settings.get('tp_enabled', True) and row['pnl_val'] >= self._as_float(settings.get('tp')):
            trigger = 'TP'
        if not trigger:
            return 'active'

        if row['is_future']:
            result = await asyncio.to_thread(
                client.close_futures_position,
                row['market'],
                row['raw_amt'],
                row['side'],
                row['hedged'],
            )
            action_name = 'CLOSED'
        else:
            result = await asyncio.to_thread(
                client.create_market_sell_order, row['market'], row['raw_amt']
            )
            action_name = 'SOLD'

        if result:
            self.text_log(f"{action_name} {row['sym']} ({trigger})")
            self.add_trade_history(ex, row['sym'], row['pnl_val'], row['revenue'], trigger)
            return 'SOLD'
        return 'active'

    def add_trade_history(self, ex, sym, pnl, revenue, type_):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.trade_history.append({
            'ts': ts,
            'ex': ex,
            'sym': sym,
            'pnl': pnl,
            'revenue': revenue,
            'type': type_
        })

    def clear_history(self):
        self.trade_history = []
        self.update_history_table()

    def update_dashboard(self, rows, total_krw, ex_sums):
        # The background task starts before a browser opens the page.
        if self.metrics_container is None or self.table_container is None:
            return

        # Update Metrics
        self.metrics_container.clear()
        with self.metrics_container:
            ui.label(f"TOTAL: ₩{total_krw:,.0f}").classes('text-green-400 font-bold')
            for ex, val in ex_sums.items():
                ui.label(f"{ex.upper()}: ₩{val:,.0f}").classes('text-gray-400 text-sm')

        # Update Table
        self.table_container.clear()
        with self.table_container:
            # Header
            with ui.row().classes('w-full bg-slate-700 p-2 rounded text-xs font-bold text-gray-300'):
                ui.label('Ex').classes('w-1/12')
                ui.label('Type').classes('w-2/12')
                ui.label('Coin').classes('w-1/12')
                ui.label('Amt').classes('w-1/12')
                ui.label('Value').classes('w-2/12')
                ui.label('Avg').classes('w-1/12')
                ui.label('Cur').classes('w-1/12')
                ui.label('PNL').classes('w-1/12')
                ui.label('State').classes('w-1/12')
                ui.label('Act').classes('w-1/12')

            # Rows
            for r in rows:
                bg_color = 'bg-slate-800'
                if r['status'] == 'active': bg_color = 'bg-green-900/20'
                elif r['status'] == 'SOLD': bg_color = 'bg-blue-900/20'
                
                with ui.row().classes(f'w-full {bg_color} p-2 border-b border-gray-700 items-center text-sm hover:bg-slate-700 transition'):
                    ui.label(r['ex'].upper()).classes('w-1/12 pl-1')
                    ui.label(r['kind']).classes('w-2/12 text-xs text-cyan-300')
                    ui.label(r['sym']).classes('w-1/12 font-bold')
                    ui.label(r['amt']).classes('w-1/12 text-xs')
                    ui.label(r['val']).classes('w-2/12 text-gray-300')
                    ui.label(r['avg']).classes('w-1/12 text-xs text-gray-400')
                    ui.label(r['cur']).classes('w-1/12 text-xs text-gray-400')
                    
                    # PNL Color
                    pnl_class = 'text-green-400' if r['pnl_val'] > 0 else 'text-red-400' if r['pnl_val'] < 0 else 'text-gray-400'
                    ui.label(r['pnl']).classes(f'w-1/12 font-bold {pnl_class}')
                    
                    # Status Icon
                    status_icon = '🟢' if r['status'] == 'active' else '🔴' if r['status'] == 'off' else '🔵' if r['status'] == 'SOLD' else '💼' if r['status'] == 'wallet' else '⚪'
                    ui.label(f"{status_icon} {r['status']}").classes('w-1/12 text-xs')
                    
                    # Action Button
                    with ui.column().classes('w-1/12'):
                        if r['action'] == 'close':
                            ui.button('Close', on_click=lambda _, x=r: self.manual_action(x)).props('dense flat color=red size=sm icon=close')
                        elif r['action'] == 'sell':
                            ui.button('Sell', on_click=lambda _, x=r: self.manual_action(x)).props('dense flat color=red size=sm icon=currency_exchange')
                        else:
                            ui.label('-').classes('text-gray-500')

        # Update History Table
        self.update_history_table()

    def update_history_table(self):
        if self.history_container is None:
            return
        self.history_container.clear()
        with self.history_container:
             # Header
            with ui.row().classes('w-full bg-slate-700 p-2 rounded text-xs font-bold text-gray-300'):
                ui.label('Time').classes('w-1/6')
                ui.label('Ex').classes('w-1/12')
                ui.label('Coin').classes('w-1/12')
                ui.label('PNL %').classes('w-1/6')
                ui.label('Revenue').classes('w-1/4')
                ui.label('Type').classes('w-1/6')
            
            # Rows (Reverse order to show latest first)
            for h in reversed(self.trade_history):
                with ui.row().classes('w-full bg-slate-800 p-2 border-b border-gray-700 items-center text-sm hover:bg-slate-700 transition'):
                     ui.label(h['ts']).classes('w-1/6 text-xs text-gray-400')
                     ui.label(h['ex'].upper()).classes('w-1/12 pl-1')
                     ui.label(h['sym']).classes('w-1/12 font-bold')
                     
                     pnl_class = 'text-green-400' if h['pnl'] > 0 else 'text-red-400' if h['pnl'] < 0 else 'text-gray-400'
                     ui.label(f"{h['pnl']:.2f}%").classes(f'w-1/6 font-bold {pnl_class}')
                     
                     rev_class = 'text-green-400' if h['revenue'] > 0 else 'text-red-400' if h['revenue'] < 0 else 'text-gray-400'
                     ui.label(f"₩{h['revenue']:,.0f}").classes(f'w-1/4 font-bold {rev_class}')

                     type_color = 'text-red-400' if h['type'] == 'SL' else 'text-green-400' if h['type'] == 'TP' else 'text-blue-400'
                     ui.label(h['type']).classes(f'w-1/6 font-bold {type_color}')


    async def manual_action(self, row_data):
        client = self.clients.get(row_data['ex'], {}).get(row_data['client_scope'])
        if not client:
            ui.notify(f"No active client for {row_data['ex']}", type='negative')
            return

        if row_data['is_future']:
            res = await asyncio.to_thread(
                client.close_futures_position,
                row_data['market'],
                row_data['raw_amt'],
                row_data['side'],
                row_data['hedged'],
            )
            action_name = 'Closed'
        else:
            res = await asyncio.to_thread(
                client.create_market_sell_order, row_data['market'], row_data['raw_amt']
            )
            action_name = 'Sold'

        if res:
            self.text_log(f"Manual {action_name} {row_data['sym']} Success")
            ui.notify(f"{action_name} {row_data['sym']}", type='positive')
            self.add_trade_history(
                row_data['ex'], row_data['sym'], row_data['pnl_val'], row_data['revenue'], 'Manual'
            )
        else:
            ui.notify(f"Failed to {action_name.lower()} {row_data['sym']}", type='negative')

    def set_update_interval(self, e):
        config['global_settings']['update_interval'] = e.value
        self.save_config()
        self.text_log(f"Update Interval changed to {e.value} sec")
        # Update timer interval immediately
        # self.update_timer.interval = e.value # No longer using ui.timer

    def setup_ui(self):
        ui.page_title('Crypto TPSL Bot (v5)')
        ui.query('body').style('background-color: #1e1e1e; color: #e0e0e0') # Dark mode theme hint

        # --- HEADER ---
        with ui.header().classes('items-center justify-between bg-slate-900 text-white'):
            ui.label('Crypto TPSL Bot (v5)').classes('text-xl font-bold')
            self.metrics_container = ui.row().classes('gap-4')

        # --- LEFT DRAWER (CONTROLS) ---
        with ui.left_drawer(value=True).classes('bg-slate-800 text-white q-pa-md'):
            ui.label('⚙️ Controls').classes('text-lg font-bold mb-4')
            
            # Global Switch
            with ui.row().classes('items-center w-full justify-between no-wrap q-py-xs'):
                ui.label('Global Auto-Trading').classes('text-sm')
                sw = ui.switch(value=self.running).on_value_change(lambda e: self.toggle_global(e.value)).props('dense size=xs color=green')
                sw.bind_value_to(self, 'running')
            
            # Update Interval
            ui.number('Interval (sec)', value=config['global_settings']['update_interval'], min=1, max=60).on_value_change(self.set_update_interval).props('dense outlined input-class=text-center').classes('w-full q-my-xs text-xs')

            # Exchanges
            for ex in ["binance", "okx", "bithumb", "upbit"]:
                with ui.expansion(ex.upper(), icon='currency_exchange').classes('w-full mb-2 bg-slate-700 rounded'):
                    with ui.column().classes('w-full q-pa-sm'):
                        if ex in ('binance', 'okx'):
                            ui.label('Spot + USDT Futures/Swap are refreshed together').classes('text-xs text-cyan-300')
                        
                        # Creds
                        ui.input('API Key').bind_value(config['exchanges'][ex], 'api_key').props('type=password dense').classes('w-full')
                        ui.input('Secret').bind_value(config['exchanges'][ex], 'secret_key').props('type=password dense').classes('w-full')
                        if ex == 'okx':
                            ui.input('Passphrase').bind_value(config['exchanges'][ex], 'password').props('type=password dense').classes('w-full')
                        
                        ui.button('Save Keys', on_click=lambda _, e=ex: self.save_keys(e)).props('dense flat color=primary').classes('w-full my-2')

                        # Connect/Disconnect
                        with ui.row().classes('w-full'):
                            ui.button('Connect', on_click=lambda _, e=ex: self.connect_exchange(e)).props('dense color=green').bind_visibility_from(self.connected, ex, backward=lambda x: not x)
                            ui.button('Disconnect', on_click=lambda _, e=ex: self.disconnect_exchange(e)).props('dense color=red').bind_visibility_from(self.connected, ex)

                        ui.separator().classes('my-2')
                        
                        # SL/TP
                        with ui.row().classes('items-center w-full'):
                            ui.switch('SL').bind_value(config['exchanges'][ex], 'sl_enabled').on_value_change(self.save_config)
                            ui.number('SL %', value=config['exchanges'][ex]['sl'], format='%.1f').bind_value(config['exchanges'][ex], 'sl').on_value_change(self.save_config).classes('w-20')
                        
                        with ui.row().classes('items-center w-full'):
                            ui.switch('TP').bind_value(config['exchanges'][ex], 'tp_enabled').on_value_change(self.save_config)
                            ui.number('TP %', value=config['exchanges'][ex]['tp'], format='%.1f').bind_value(config['exchanges'][ex], 'tp').on_value_change(self.save_config).classes('w-20')
                        
                        # Whitelist
                        ui.textarea('Whitelist (csv)').bind_value(config['exchanges'][ex], 'whitelist', forward=lambda x: ", ".join(x) if isinstance(x, list) else x, backward=lambda x: [s.strip().upper() for s in x.split(',') if s.strip()] if isinstance(x, str) else x).on_value_change(self.save_config).classes('w-full text-xs')


            ui.separator().classes('my-4')
            ui.label('Logs').classes('text-lg font-bold')
            self.log_area = ui.log(max_lines=100).classes('w-full h-64 bg-slate-900 separate-logs rounded p-2 text-xs font-mono')

        # --- MAIN AREA ---
        with ui.column().classes('w-full h-screen q-pa-md'):
            # Dashboard Section
            with ui.column().classes('w-full h-2/3'):
                ui.label('📊 Dashboard').classes('text-xl font-bold mb-4')
                self.table_container = ui.column().classes('w-full h-full scroll')
            
            ui.separator().classes('my-6')
            
            # Transaction History Section
            with ui.column().classes('w-full h-1/3'):
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('📜 Transaction History').classes('text-xl font-bold')
                    ui.button('Clear History', on_click=self.clear_history).props('dense flat color=red icon=delete')
                self.history_container = ui.column().classes('w-full h-full scroll')

    def toggle_global(self, value):
        config['global_settings']['auto_trading'] = value
        self.save_config()
        self.text_log(f"Global Switch: {value}")
        
    def save_keys(self, ex):
        self.save_config()
        ui.notify(f"{ex} Keys Saved", type='positive')

def run_app():
    app_instance = TpslApp()
    app.on_startup(lambda: asyncio.create_task(app_instance.run_background_loop()))

    @ui.page('/')
    def index():
        app_instance.setup_ui()
        app_instance.setup_logger()

    ui.run(title='Crypto TPSL Bot', port=8080, dark=True, host='127.0.0.1')


if __name__ in {'__main__', '__mp_main__'}:
    run_app()
