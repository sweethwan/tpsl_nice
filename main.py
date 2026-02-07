import streamlit as st
import pandas as pd
import time
import logging
from datetime import datetime

# Local imports
import config_manager
import logger_setup
from crypto_logics import CryptoLogic

# --- ROBUST LOGGING SETUP ---
if 'log_history' not in st.session_state:
    st.session_state.log_history = []


# Setup Logger
logger = logger_setup.setup_logger()

# --- Page Config ---
st.set_page_config(page_title="Crypto TPSL Bot (v5)", layout="wide")

# --- Session State ---
if 'running' not in st.session_state: st.session_state.running = False
if 'running' not in st.session_state: st.session_state.running = False
if 'hidden_coins' not in st.session_state: st.session_state.hidden_coins = set()



# Connection States
for ex in ["binance", "okx", "bithumb", "upbit"]:
    if f"connected_{ex}" not in st.session_state:
        st.session_state[f"connected_{ex}"] = False
    if f"client_{ex}" not in st.session_state:
        st.session_state[f"client_{ex}"] = None

def text_log(msg):
    logger.info(msg) # This triggers the Handler -> Session State

def hide_coin_callback(target):
    st.session_state.hidden_coins.add(target)
    # Force update
    st.session_state.hidden_coins = set(st.session_state.hidden_coins)
    text_log(f"Hiding {target} (Callback). Total: {len(st.session_state.hidden_coins)}")




# --- Load Config ---
config = config_manager.load_config()

# Helper: Migration for new keys
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

# Global Settings Migration
if 'global_settings' not in config:
    config['global_settings'] = {'auto_trading': False, 'update_interval': 5}
    migrated = True
elif 'update_interval' not in config['global_settings']:
    config['global_settings']['update_interval'] = 5
    migrated = True

if migrated:
    config_manager.save_config(config)

# Rate Helper
@st.cache_data(ttl=60)
def get_exchange_rate():
    try:
        import requests
        url = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"
        data = requests.get(url).json()
        return float(data[0]['trade_price'])
    except:
        return 1400.0
usdt_krw = get_exchange_rate()

# --- APP LAYOUT ---
st.title("Crypto TPSL Bot (v5)")

# Top Dashboard
st.subheader("💰 Total Assets (Estimated KRW)")
total_krw = 0.0
metrics_cols = st.columns(5) 

# Trigger Logic: Global is Master. 
should_loop = st.session_state.running

col_left, col_right = st.columns([1, 2.5])

# === LEFT: CONTROLS ===
with col_left:
    st.header("⚙️ Controls")
    
    # Global Switch
    st.info("GLOBAL MASTER SWITCH")
    global_auto = st.toggle("Global Auto-Trading", value=st.session_state.running, key="global_sw")
    if global_auto != st.session_state.running:
        st.session_state.running = global_auto
        text_log(f"Global Switch: {global_auto}")

    st.divider()

    # Update Interval Setting
    curr_interval = config.get('global_settings', {}).get('update_interval', 5)
    new_interval = st.number_input("Update Interval (sec)", 1, 60, int(curr_interval), 1, key="upd_int")
    if new_interval != curr_interval:
        if 'global_settings' not in config: config['global_settings'] = {}
        config['global_settings']['update_interval'] = new_interval
        config_manager.save_config(config)
        st.caption("Lower specific interval may cause screen dimming.")
        st.rerun()

    st.divider()

    # Exchange Settings
    for ex in ["binance", "okx", "bithumb", "upbit"]:
        with st.expander(f"{ex.upper()} Settings", expanded=False):
            
            # 1. Market Type (Binance Only for now, expand others if needed)
            if ex == 'binance':
                m_type = st.radio("Market Type", ["spot", "future"], 
                                  index=0 if config['exchanges'][ex]['market_type']=='spot' else 1,
                                  key=f"mt_{ex}")
                # Auto-Save on Change
                if m_type != config['exchanges'][ex]['market_type']:
                    config['exchanges'][ex]['market_type'] = m_type
                    config_manager.save_config(config)
                    text_log(f"Binance Market Type -> {m_type}")
                    # If connected, might need reconnect warning?
                    if st.session_state[f"connected_{ex}"]:
                        st.warning("Please Disconnect & Reconnect for change to take effect.")

            # 2. Connection
            c1, c2 = st.columns(2)
            is_con = st.session_state[f"connected_{ex}"]
            
            if not is_con:
                ak = config['exchanges'][ex]['api_key'] 
                # If keys missing, show inputs? NO, user wants inputs.
                # Let's put inputs in a sub-expander or just here if not connected?
                # To keep UI clean, let's keep keys in a sub-disclosure or modal?
                # Using existing pattern:
                with st.form(f"cred_{ex}"):
                    ak_in = st.text_input("API Key", value=config['exchanges'][ex]['api_key'], type="password")
                    sk_in = st.text_input("Secret", value=config['exchanges'][ex]['secret_key'], type="password")
                    if ex=='okx': 
                        pass_in = st.text_input("Passphrase", value=config['exchanges'][ex].get('password',''), type="password")
                    if st.form_submit_button("Save Keys"):
                        config['exchanges'][ex]['api_key'] = ak_in
                        config['exchanges'][ex]['secret_key'] = sk_in
                        if ex=='okx': config['exchanges'][ex]['password'] = pass_in
                        config_manager.save_config(config)
                        st.success("Saved")

                if c1.button("Connect", key=f"btn_con_{ex}"):
                    # Connect logic
                    creds = config['exchanges'][ex]
                    mtype = creds.get('market_type', 'spot')
                    client = CryptoLogic(ex, creds['api_key'], creds['secret_key'], creds.get('password'), mtype)
                    if client.exchange:
                        st.session_state[f"client_{ex}"] = client
                        st.session_state[f"connected_{ex}"] = True
                        text_log(f"Connected {ex} ({mtype})")
                        st.rerun()
                    else:
                        st.error("Failed")
            else:
                if c2.button("Disconnect", key=f"btn_dis_{ex}"):
                    st.session_state[f"client_{ex}"] = None
                    st.session_state[f"connected_{ex}"] = False
                    text_log(f"Disconnected {ex}")
                    st.rerun()
                st.success("● Connected")

            # 3. SL/TP Settings (Auto-Save Toggles)
            st.markdown("---")
            st.caption("Auto-Trading Parameters")
            
            # SL
            c_sl_tog, c_sl_val = st.columns([1, 2])
            sl_en = c_sl_tog.toggle("SL On", value=config['exchanges'][ex]['sl_enabled'], key=f"sl_en_{ex}")
            sl_val = c_sl_val.number_input("SL %", 0.1, 999.0, float(config['exchanges'][ex]['sl']), 0.1, format="%.1f", key=f"sl_val_{ex}")
            
            # TP
            c_tp_tog, c_tp_val = st.columns([1, 2])
            tp_en = c_tp_tog.toggle("TP On", value=config['exchanges'][ex]['tp_enabled'], key=f"tp_en_{ex}")
            tp_val = c_tp_val.number_input("TP %", 0.1, 999.0, float(config['exchanges'][ex]['tp']), 0.1, format="%.1f", key=f"tp_val_{ex}")

            # Whitelist
            st.markdown("---")
            wl = config['exchanges'][ex].get('whitelist', [])
            wl_str = st.text_area("Whitelist (csv)", value=", ".join(wl), key=f"wl_{ex}")

            # Unified Save Button for Settings
            if st.button("Save Settings", key=f"save_{ex}"):
                config['exchanges'][ex]['sl_enabled'] = sl_en
                config['exchanges'][ex]['sl'] = sl_val
                config['exchanges'][ex]['tp_enabled'] = tp_en
                config['exchanges'][ex]['tp'] = tp_val
                # Save Whitelist
                new_list = [s.strip().upper() for s in wl_str.split(',') if s.strip()]
                config['exchanges'][ex]['whitelist'] = new_list
                
                config_manager.save_config(config)
                text_log(f"{ex} Settings Saved. SL:{sl_en}({sl_val}%), TP:{tp_en}({tp_val}%), WL:{len(new_list)}")

    st.divider()
    st.subheader("Logs")
    # Log Window
    logs = "\n".join(st.session_state.log_history[::-1]) # Newest first? User prefers scrollability usually
    st.text_area("Logs", logs, height=300, key="log_area", label_visibility="collapsed")

    # Debug
    # st.sidebar.write("Hidden:", st.session_state.hidden_coins)


# === RIGHT: DASHBOARD ===
with col_right:
    st.header("📊 Dashboard")
    man_refresh = st.button("Refresh")

    # Fetch needed?
    active_exists = any(st.session_state[f"connected_{x}"] for x in ["binance", "okx", "bithumb", "upbit"])
    if should_loop or man_refresh or active_exists:
        
        ex_sums = {x:0.0 for x in ["binance", "okx", "bithumb", "upbit"]}
        rows = []

        for ex in ["binance", "okx", "bithumb", "upbit"]:
            if st.session_state[f"connected_{ex}"]:
                client = st.session_state[f"client_{ex}"]
                if not client: continue
                
                # Logic: If Futures -> fetch_positions, Else -> fetch_balance
                try:
                    is_future = (config['exchanges'][ex].get('market_type') == 'future')
                    
                    df = pd.DataFrame()
                    
                    if is_future:
                        # Fetch BOTH Positions and Wallet Balance for Futures
                        pos_df = client.fetch_positions()
                        bal_df = client.fetch_balance()
                        
                        # Merge: We want to see USDT (Wallet) AND Positions (BTC, ETH...)
                        # Note: symbol in pos_df is usually "BTC/USDT:USDT" or "BTC/USDT" depending on ccxt version
                        # symbol in bal_df is "USDT", "BNB"
                        
                        df = pd.concat([pos_df, bal_df], ignore_index=True)
                    else:
                        df = client.fetch_balance()
                        
                except Exception as e:
                    text_log(f"Fetch Error {ex}: {e}")
                    continue

                if not df.empty:
                    for i, row in df.iterrows():
                        sym = row['Symbol']
                        amt = row['Total']
                        
                        # Filter zero amounts just in case
                        if amt == 0: continue

                        # 1. Fiat/Quote Asset Handling (Assets Sum)
                        # In Futures, this is usually USDT / BUSD (Margin Balance)
                        if sym in ['USD','USDT','KRW','USDC']:
                             # It's currency, not a position (usually)
                             # Unless it's a position on USDT? No.
                             rate = 1.0 if sym=='KRW' else usdt_krw
                             val = amt * rate
                             ex_sums[ex] += val
                             total_krw += val
                             
                             # Filter: Hide Quote Currency from Dashboard List?
                             # User request: "해외 거래소는 usdt 국내는 krw 대시보드에 안뜨게 적용"
                             is_domestic = ex in ['bithumb', 'upbit']
                             if (is_domestic and sym == 'KRW') or (not is_domestic and sym == 'USDT'):
                                 continue

                        
                        # 2. Market Symbol & Price Logic
                        market_sym = f"{sym}/USDT" if ex in ['binance','okx'] else f"{sym}/KRW"
                        
                        # Smart Symbol Guessing for Futures vs Spot
                        if is_future:
                            # Positions often come as full symbols e.g. "BTC/USDT"
                            # Balances come as "USDT"
                            if '/' in sym: # It's likely a position with pair name
                                market_sym = sym
                                # Strip for display if needed? Keep full for clarity
                            elif sym in ['USDT', 'BUSD', 'USDC']: # Wallet
                                # It's just quote asset, no "Current Price" needed (it's 1.0)
                                cur_price = 1.0
                                market_sym = None # No market to trade
                            else:
                                # It might be a coin-margin asset like BTC in COIN-M? 
                                # But we are in USD-M mode. 
                                # If we see "BTC" in wallet here, it's weird for USD-M unless multi-asset mode.
                                # Let's assume standard linear.
                                market_sym = f"{sym}/USDT"
                        else:
                            # Spot
                            market_sym = client.get_market_symbol(sym)
                        
                        # Filter: Ignore specific non-tradable symbols explicitly (like 'P' from Bithumb)
                        if sym in ['P', 'POINT']: 
                            continue

                        
                        # Fetch Price
                        cur_price = 0.0
                        if market_sym:
                            ticker_res = client.fetch_ticker(market_sym)
                            if ticker_res is not None:
                                cur_price = ticker_res
                        elif sym in ['USDT','USDC','USD']:
                            cur_price = 1.0

                        avg_price = row.get('AvgPrice', 0)
                        
                        # Value Calc
                        val_native = amt * (cur_price if cur_price else 0)
                        
                        # Add to Sums (avoid double counting if we already did above for fiat)
                        # We handled USDT above? 
                        # Wait, logic above `if sym in [...] continue` skipped the specific row processing.
                        # I removed the `continue` pattern to unify display.
                        
                        rate = 1.0 if ex in ['upbit','bithumb'] else usdt_krw
                        val_krw = val_native * rate
                        
                        # Filter: Small Amount (< 10,000 KRW or < 10 USD approx)
                        # 10 USD is approx 14,000 KRW. Let's strictly use KRW value for uniformity or USD for global.
                        # User said: "원화기준 만원 달러기준 10달러 이하"
                        # 10,000 KRW check:
                        if val_krw < 10000:
                            continue

                        
                        # If we already added USDT in the "is asset" check, don't double count?
                        # Let's simplify: Just calculate everything here.
                        # BUT, ensure we don't double count if `df` has duplicates? Unlikely.
                        
                        # reset sums for this row to be safe? 
                        # Actually the previous loop structure accumulated ex_sums. 
                        # I'll just add to ex_sums here.
                        # NOTE: `ex_sums` was initialized to 0.
                        
                        ex_sums[ex] += val_krw
                        total_krw += val_krw
                        
                        # Special Case: Binance Futures
                        # Do NOT add position value (leverage) to Total Assets, only Wallet Balance.
                        # We identify positions by having '/' in symbol (e.g. BTC/USDT).
                        if ex == 'binance' and is_future and '/' in sym:
                            ex_sums[ex] -= val_krw
                            total_krw -= val_krw

                        # PNL
                        pnl_pct = 0.0
                        pnl_str = "-"
                        # Only calc PNL if we have a valid entry price and it's a trading pair (not USDT)
                        if avg_price and avg_price > 0 and cur_price and market_sym:
                            pnl_pct = ((cur_price - avg_price) / avg_price) * 100
                            pnl_str = f"{pnl_pct:.2f}%"

                        # Auto Trade Logic
                        triggers = []
                        whitelist = config['exchanges'][ex].get('whitelist', [])
                        
                        # Symbol check for whitelist: 
                        # If sym is "BTC/USDT", user might have "BTC" in whitelist.
                        # We should check both or normalize.
                        base_sym = sym.split('/')[0] if '/' in sym else sym
                        
                        in_wl = False
                        if not whitelist:
                            # Empty whitelist => ALL coins active
                            in_wl = True
                        else:
                            # Check exact or base
                            if sym.upper() in whitelist or base_sym.upper() in whitelist:
                                in_wl = True
                        
                        # Valid Trigger Condition:
                        # 1. Loop Active
                        # 2. Whitelisted
                        # 3. Not a Fiat/Stable (PNL must exist)
                        # 4. Market Symbol exists
                        
                        if should_loop and in_wl and pnl_str != "-" and market_sym:
                            sl_enabled = config['exchanges'][ex]['sl_enabled']
                            tp_enabled = config['exchanges'][ex]['tp_enabled']
                            sl_limit = config['exchanges'][ex]['sl']
                            tp_limit = config['exchanges'][ex]['tp']

                            if sl_enabled and pnl_pct <= -sl_limit:
                                triggers.append("SL")
                                res = client.create_market_sell_order(market_sym, amt)
                                if res: text_log(f"SOLD {sym} (SL)")
                            
                            if tp_enabled and pnl_pct >= tp_limit:
                                triggers.append("TP")
                                res = client.create_market_sell_order(market_sym, amt)
                                if res: text_log(f"SOLD {sym} (TP)")

                        # Determine Status for UI
                        status_ui = "wait"
                        if triggers: status_ui = "SOLD" # If SL/TP triggered, set status to SOLD
                        elif not should_loop: status_ui = "off"
                        elif in_wl: status_ui = "active"
                        
                        # Add Row
                        rows.append({
                            "ex": ex, "sym": sym, "amt": amt, 
                            "val": f"₩{val_krw:,.0f}",
                            "avg": f"{avg_price:.4f}", 
                            "cur": f"{cur_price:.4f}", 
                            "pnl": pnl_str,
                            "msg": ", ".join(triggers),
                            "market": market_sym,
                            "status": status_ui,
                            "id": f"{ex}_{sym}"
                        })


        # Render Metrics
        with metrics_cols[0]: st.metric("TOTAL ASSETS", f"₩{total_krw:,.0f}")
        for i, ex in enumerate(["binance","okx","bithumb","upbit"]):
            with metrics_cols[i+1]: st.metric(ex.upper(), f"₩{ex_sums[ex]:,.0f}")
        
        st.divider()

        # Render Table
        # Render Table
        if rows:
            # DEBUG: Show IDs
            # st.error(f"DEBUG DATA: Hidden={st.session_state.hidden_coins}")
            # st.write("First 3 Row IDs:", [r['id'] for r in rows[:3]])
            
            # Filter hidden
            rows = [r for r in rows if r['id'] not in st.session_state.hidden_coins]

        if rows:
            # ex, sym, amt, val, avg, cur, pnl, STATUS, act, del
            h = st.columns([0.8, 1, 1, 1.2, 1, 1, 0.8, 1.0, 0.8])
            h[0].write("**Ex**"); h[1].write("**Coin**"); h[2].write("**Amt**"); h[3].write("**Value**")
            h[4].write("**Avg**"); h[5].write("**Cur**"); h[6].write("**PNL**"); h[7].write("**State**"); h[8].write("**Act**")
            
            for r in rows:
                with st.container():
                    c = st.columns([0.8, 1, 1, 1.2, 1, 1, 0.8, 1.0, 0.8])
                    c[0].write(r['ex'].upper())

                    c[1].write(r['sym'])
                    c[2].write(f"{r['amt']:.4f}")
                    c[3].write(r['val'])
                    c[4].write(r['avg'])
                    c[5].write(r['cur'])
                    
                    if r['pnl'] != '-':
                        v = float(r['pnl'].replace('%',''))
                        if v > 0: c[6].success(r['pnl'])
                        elif v < 0: c[6].error(r['pnl'])
                        else: c[6].write(r['pnl'])
                    else: c[6].write("-")

                    # Status Code
                    # Status Code
                    status_ph = c[7].empty() # Placeholder for status updates
                    if r['status'] == 'active':
                        status_ph.success("🟢 Run")
                    elif r['status'] == 'off':
                        status_ph.error("🔴 GlobalOff")
                    elif r['status'] == 'wait':
                        status_ph.caption("⚪ Not in WL")
                    elif r['status'] == 'SOLD':
                        status_ph.info("SOLD")
                    else:
                        status_ph.caption(r['status'])

                    if c[8].button("Sell", key=f"s_{r['ex']}_{r['sym']}"): # Sell/Liq
                         client = st.session_state[f"client_{r['ex']}"]
                         res = client.create_market_sell_order(r['market'], r['amt'])
                         if res:
                             status_ph.info("SOLD") # Update UI immediately
                             st.success("Sold")

                    


                    st.divider()

    if should_loop:
        interval = config.get('global_settings', {}).get('update_interval', 5)
        # Check active trading logic here if needed, otherwise just sleep
        time.sleep(interval)
        st.rerun()
