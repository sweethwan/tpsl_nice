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
        self.clients = {}
        self.connected = {ex: False for ex in ["binance", "okx", "bithumb", "upbit"]}
        
        # UI Elements
        self.log_area = None
        self.table_container = None
        self.metrics_container = None
        
        # UI Setup moved to @ui.page('/') handler
        # self.setup_ui()
        # self.setup_logger()
        
        # Start Update Loop
        self.update_timer = ui.timer(config['global_settings']['update_interval'], self.update_loop)

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
        mtype = creds.get('market_type', 'spot')
        try:
            client = CryptoLogic(ex, creds['api_key'], creds['secret_key'], creds.get('password'), mtype)
            if client.exchange:
                self.clients[ex] = client
                self.connected[ex] = True
                self.text_log(f"Connected {ex} ({mtype})")
                ui.notify(f"Connected {ex}", type='positive')
            else:
                ui.notify(f"Failed to connect {ex}", type='negative')
        except Exception as e:
            self.text_log(f"Connection Error {ex}: {e}")
            ui.notify(f"Error {ex}", type='negative')
            
    def disconnect_exchange(self, ex):
        self.clients[ex] = None
        self.connected[ex] = False
        self.text_log(f"Disconnected {ex}")
        ui.notify(f"Disconnected {ex}", type='info')

    async def update_loop(self):
        # Only run if connected to at least one exchange or manual refresh needed?
        # Actually in NiceGUI we want real-time updates if possible.
        # But we respect the interval.
        
        if not any(self.connected.values()):
            return

        try:
            # 1. Fetch Rate
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

            for ex, client in list(self.clients.items()):
                if not client: continue
                
                try:
                    is_future = (config['exchanges'][ex].get('market_type') == 'future')
                    df = pd.DataFrame()

                    if is_future:
                        # Async wrapper for blocking calls
                        pos_df = await asyncio.to_thread(client.fetch_positions)
                        bal_df = await asyncio.to_thread(client.fetch_balance)
                        df = pd.concat([pos_df, bal_df], ignore_index=True)
                    else:
                        df = await asyncio.to_thread(client.fetch_balance)
                except Exception as e:
                    self.text_log(f"Fetch Error {ex}: {e}")
                    continue

                if not df.empty:
                    for i, row in df.iterrows():
                        sym = row['Symbol']
                        amt = row['Total']
                        if amt == 0: continue

                        # Filter Ignored Symbols (Points, Airdrops, etc)
                        if sym in ['P', 'POINT', 'APENFT']:
                            continue

                        # Value Calc Logic similar to Streamlit
                        is_domestic = ex in ['bithumb', 'upbit']
                        
                        # Filter Quote Assets from Dashboard
                        if (is_domestic and sym == 'KRW') or (not is_domestic and sym == 'USDT'):
                             # But we still need to add to Total Assets
                             val = amt * (1.0 if sym=='KRW' else usdt_krw)
                             ex_sums[ex] += val
                             total_krw += val
                             continue

                        # Market Symbol
                        market_sym = f"{sym}/USDT" if ex in ['binance','okx'] else f"{sym}/KRW"
                        cur_price = 0.0
                        
                        if is_future:
                             if '/' in sym: market_sym = sym
                             elif sym in ['USDT', 'BUSD']: 
                                 cur_price = 1.0; market_sym = None

                        # Fetch Price
                        if market_sym:
                            try:
                                cur_price = await asyncio.to_thread(client.fetch_ticker, market_sym) or 0.0
                            except: cur_price = 0.0
                        elif sym in ['USDT','USDC']: cur_price = 1.0

                        avg_price = row.get('AvgPrice', 0)
                        val_native = amt * cur_price
                        rate = 1.0 if is_domestic else usdt_krw
                        val_krw = val_native * rate

                        # Filter Small Amounts (< 10,000 KRW)
                        if val_krw < 10000: continue

                        ex_sums[ex] += val_krw
                        total_krw += val_krw
                        
                        # Binance Futures Position handling (adjust total)
                        if ex == 'binance' and is_future and '/' in sym:
                            ex_sums[ex] -= val_krw
                            total_krw -= val_krw


                        # PNL
                        pnl_pct = 0.0
                        pnl_amt_krw = 0.0
                        if avg_price > 0 and cur_price > 0 and market_sym:
                            pnl_pct = ((cur_price - avg_price) / avg_price) * 100
                            pnl_amt_krw = (cur_price - avg_price) * amt * rate

                        # Auto Trade Logic
                        triggers = []
                        # Check Whitelist
                        wl = config['exchanges'][ex].get('whitelist', [])
                        base_sym = sym.split('/')[0] if '/' in sym else sym
                        in_wl = not wl or (sym.upper() in wl or base_sym.upper() in wl)

                        status_ui = "wait"
                        
                        if self.running and in_wl and market_sym:
                            sl_enabled = config['exchanges'][ex]['sl_enabled']
                            tp_enabled = config['exchanges'][ex]['tp_enabled']
                            sl_limit = config['exchanges'][ex]['sl']
                            tp_limit = config['exchanges'][ex]['tp']

                            if sl_enabled and pnl_pct <= -sl_limit:
                                triggers.append("SL")
                                res = await asyncio.to_thread(client.create_market_sell_order, market_sym, amt)
                                if res: 
                                    self.text_log(f"SOLD {sym} (SL)")
                                    self.add_trade_history(ex, sym, pnl_pct, pnl_amt_krw, "SL")
                            
                            if tp_enabled and pnl_pct >= tp_limit:
                                triggers.append("TP")
                                res = await asyncio.to_thread(client.create_market_sell_order, market_sym, amt)
                                if res: 
                                    self.text_log(f"SOLD {sym} (TP)")
                                    self.add_trade_history(ex, sym, pnl_pct, pnl_amt_krw, "TP")

                        if triggers: status_ui = "SOLD"
                        elif self.running and in_wl: status_ui = "active"
                        elif not self.running: status_ui = "off"

                        rows.append({
                            'ex': ex, 'sym': sym, 'amt': f"{amt:.4f}",
                            'val': f"₩{val_krw:,.0f}",
                            'avg': f"{avg_price:.4f}", 'cur': f"{cur_price:.4f}",
                            'pnl': f"{pnl_pct:.2f}%", 'pnl_val': pnl_pct,
                            'revenue': pnl_amt_krw,
                            'status': status_ui,
                            'market': market_sym,
                            'raw_amt': amt
                        })

            # Update UI
            self.update_dashboard(rows, total_krw, ex_sums)

        except RuntimeError as e:
            if "client" in str(e) and "deleted" in str(e):
                pass # Ignore client deletion errors during reload
            else:
                self.text_log(f"Loop Runtime Error: {e}")
        except Exception as e:
            self.text_log(f"Loop Error: {e}")

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
                    ui.label(r['sym']).classes('w-1/12 font-bold')
                    ui.label(r['amt']).classes('w-1/12 text-xs')
                    ui.label(r['val']).classes('w-2/12 text-gray-300')
                    ui.label(r['avg']).classes('w-1/12 text-xs text-gray-400')
                    ui.label(r['cur']).classes('w-1/12 text-xs text-gray-400')
                    
                    # PNL Color
                    pnl_class = 'text-green-400' if r['pnl_val'] > 0 else 'text-red-400' if r['pnl_val'] < 0 else 'text-gray-400'
                    ui.label(r['pnl']).classes(f'w-1/12 font-bold {pnl_class}')
                    
                    # Status Icon
                    status_icon = '🟢' if r['status'] == 'active' else '🔴' if r['status'] == 'off' else '🔵' if r['status'] == 'SOLD' else '⚪'
                    ui.label(f"{status_icon} {r['status']}").classes('w-1/12 text-xs')
                    
                    # Action Button
                    with ui.column().classes('w-1/12'):
                         ui.button('Sell', on_click=lambda _, x=r: self.manual_sell(x)).props('dense flat color=red size=sm icon=currency_exchange')

        # Update History Table
        self.update_history_table()

    def update_history_table(self):
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


    async def manual_sell(self, row_data):
        client = self.clients.get(row_data['ex'])
        if client:
            res = await asyncio.to_thread(client.create_market_sell_order, row_data['market'], row_data['raw_amt'])
            if res: 
                self.text_log(f"Manual Sell {row_data['sym']} Success")
                ui.notify(f"Sold {row_data['sym']}", type='positive')
                self.add_trade_history(row_data['ex'], row_data['sym'], row_data['pnl_val'], row_data['revenue'], "Manual")

    def set_update_interval(self, e):
        config['global_settings']['update_interval'] = e.value
        self.save_config()
        self.text_log(f"Update Interval changed to {e.value} sec")
        # Update timer interval immediately
        self.update_timer.interval = e.value

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
                        # Market Type (Binance Only)
                        if ex == 'binance':
                            ui.select(['spot', 'future'], value=config['exchanges'][ex]['market_type'], label='Market Type').bind_value(config['exchanges'][ex], 'market_type').on_value_change(self.save_config).classes('w-full')
                        
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

# Init App
app_instance = TpslApp()

@ui.page('/')
def index():
    app_instance.setup_ui()
    app_instance.setup_logger()

ui.run(title='Crypto TPSL Bot', port=8080, dark=True)
