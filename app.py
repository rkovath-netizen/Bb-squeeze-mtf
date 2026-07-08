import streamlit as st
import pandas as pd
import pandas_ta as ta
import upstox_client
from upstox_client.rest import ApiException
import os
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="FnO Multi-Strategy Engine", page_icon="🚀", layout="wide")
st.title("⚡ FnO Multi-Strategy Momentum Dashboard")

# --- LOAD SECURE ACCESS TOKEN ---
if "UPSTOX_ANALYTICS_TOKEN" in st.secrets:
    access_token = st.secrets["UPSTOX_ANALYTICS_TOKEN"]
    st.sidebar.success("🔒 Upstox Analytics Token Connected!")
else:
    st.sidebar.error("❌ Missing Token! Add UPSTOX_ANALYTICS_TOKEN to Streamlit Secrets.")
    st.stop()

# --- STRATEGY SELECTION SWITCH ---
st.sidebar.header("🎯 Strategy Settings")
selected_strategy = st.sidebar.selectbox("Choose Core Trading Engine", ["Squeeze (Breakout/Breakdown)", "Mean Reversal"])

# Universal Inputs
bb_len = st.sidebar.number_input("BB Length", 20)
bb_std = st.sidebar.number_input("BB StdDev", 2.0)
target_pct = st.sidebar.number_input("Target Target % (For Option Spread)", value=5.0)

if selected_strategy == "Mean Reversal":
    st.sidebar.subheader("Reversal Parameters")
    rsi_len = st.sidebar.number_input("RSI Period", 14)
    rsi_ob = st.sidebar.number_input("RSI Overbought Level", 70)
    rsi_os = st.sidebar.number_input("RSI Oversold Level", 30)

# --- LOAD WATCHLIST ---
csv_filename = "fno_with_sectors.csv"
log_filename = "forward_test_log.csv"

if os.path.exists(csv_filename):
    try:
        watchlist_df = pd.read_csv(csv_filename)
        watchlist_df.columns = watchlist_df.columns.str.strip()
        
        if 'Symbol' not in watchlist_df.columns:
            st.error("Error: CSV must contain a column named 'Symbol'")
            st.stop()
            
        if 'Sector' in watchlist_df.columns:
            sectors = ["All Sectors"] + list(watchlist_df['Sector'].dropna().unique())
            selected_sector = st.sidebar.selectbox("Filter Watchlist by Sector", sectors)
            if selected_sector != "All Sectors":
                watchlist_df = watchlist_df[watchlist_df['Sector'] == selected_sector]
        
        st.sidebar.info(f"Scanning Target: {len(watchlist_df)} Stocks")
    except Exception as e:
        st.sidebar.error(f"Error reading watchlist CSV: {str(e)}")
        st.stop()
else:
    st.sidebar.warning(f"⚠️ {csv_filename} missing from repository root folder.")
    st.stop()

# --- OPTION SPREAD STRIKE PRICE CALCULATOR ---
def calculate_spread_strikes(current_price, direction="BULLISH"):
    price = float(current_price)
    if price < 150: step = 2.5
    elif price < 500: step = 5.0
    elif price < 1500: step = 10.0
    elif price < 3000: step = 20.0
    else: step = 50.0
        
    atm_strike = round(price / step) * step
    
    if direction == "BULLISH":
        target_price = price * (1 + (target_pct / 100))
        otm_strike = round(target_price / step) * step
        if otm_strike <= atm_strike: otm_strike = atm_strike + step
    else: # BEARISH
        target_price = price * (1 - (target_pct / 100))
        otm_strike = round(target_price / step) * step
        if otm_strike >= atm_strike: otm_strike = atm_strike - step
        
    return int(atm_strike), int(otm_strike)

# --- FORWARD TEST LOGGER ---
def log_trigger_to_csv(symbol, sector, entry_price, atm_strike, otm_strike, strategy_name, direction):
    now = datetime.now()
    log_data = {
        "Timestamp": [now.strftime("%Y-%m-%d %H:%M:%S")],
        "Date": [now.strftime("%Y-%m-%d")],
        "Strategy": [strategy_name],
        "Direction": [direction],
        "Symbol": [symbol],
        "Sector": [sector],
        "Entry_Price": [round(entry_price, 2)],
        "ATM_Option_Strike": [atm_strike],
        "OTM_Option_Strike": [otm_strike],
        "Status": ["OPEN"]
    }
    new_log_df = pd.DataFrame(log_data)
    
    if not os.path.exists(log_filename):
        new_log_df.to_csv(log_filename, index=False)
    else:
        existing_logs = pd.read_csv(log_filename)
        today_str = now.strftime("%Y-%m-%d")
        duplicate = existing_logs[(existing_logs['Symbol'] == symbol) & 
                                  (existing_logs['Date'] == today_str) & 
                                  (existing_logs['Strategy'] == strategy_name) &
                                  (existing_logs['Direction'] == direction)]
        if duplicate.empty:
            new_log_df.to_csv(log_filename, mode='a', header=False, index=False)

# --- FETCH DATA FROM UPSTOX ---
def get_historical_data(symbol_name, interval):
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    instrument_key = f"NSE_EQ|{str(symbol_name).strip().upper()}"
    
    try:
        api_response = api_instance.get_historical_candle_data1(instrument_key, interval, "100")
        if api_response.status == "success" and api_response.data.candles:
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(api_response.data.candles, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.iloc[::-1]  
            return df
    except ApiException:
        return None
    return pd.DataFrame()

# --- STRATEGY INDICATOR PROCESSING ---
def calculate_indicators(df):
    if df is None or df.empty: return df
    
    # Bollinger Bands
    bb = ta.bbands(df['close'], length=bb_len, std=bb_std)
    df = pd.concat([df, bb], axis=1)
    
    # Ensure correct matching keys derived dynamically matching default pandas_ta layout
    bbl = f"BBL_{int(bb_len)}_{bb_std}"
    bbu = f"BBU_{int(bb_len)}_{bb_std}"
    bbm = f"BBM_{int(bb_len)}_{bb_std}"
    
    df['bandwidth'] = (df[bbu] - df[bbl]) / df[bbm]
    df['pct_b'] = (df['close'] - df[bbl]) / (df[bbu] - df[bbl])
    df['bw_sma50'] = ta.sma(df['bandwidth'], length=50)
    df['bw_ema20'] = ta.ema(df['bandwidth'], length=20)
    
    if selected_strategy == "Mean Reversal":
        df['rsi'] = ta.rsi(df['close'], length=rsi_len)
        
    return df

# --- SCANNER DASHBOARD ENGINE ---
if st.button(f"RUN {selected_strategy.upper()} SCANNER 🚀"):
    results = []
    progress_bar = st.progress(0)
    total_stocks = len(watchlist_df)
    sec_col = 'Sector' if 'Sector' in watchlist_df.columns else None

    for index, row in watchlist_df.iterrows():
        stock_sym = row['Symbol']
        stock_sec = row[sec_col] if sec_col else "N/A"
        progress_bar.progress((index + 1) / total_stocks)
        
        df_day = get_historical_data(stock_sym, "day")
        df_1h = get_historical_data(stock_sym, "60minute")
        df_15m = get_historical_data(stock_sym, "15minute")
        
        if df_day is not None and not df_day.empty and not df_1h.empty and not df_15m.empty:
            df_day = calculate_indicators(df_day)
            df_1h = calculate_indicators(df_1h)
            df_15m = calculate_indicators(df_15m)
            
            current_close = float(df_15m['close'].iloc[-1])
            status = "❌ No Setup"
            direction = "NONE"
            atm_strike, otm_strike = 0, 0
            
            # --- EXECUTE LOGIC BLOCK A: SQUEEZE STRATEGY ---
            if selected_strategy == "Squeeze (Breakout/Breakdown)":
                daily_sqz = df_day['bandwidth'].iloc[-1] < df_day['bw_sma50'].iloc[-1]
                hourly_sqz = df_1h['bandwidth'].iloc[-1] < df_1h['bw_sma50'].iloc[-1]
                
                curr_15 = df_15m.iloc[-1]
                prev_15 = df_15m.iloc[-2]
                
                # Volatility Expansion check (Valid for both up and down breaks)
                trigger_vol = (curr_15['bandwidth'] > curr_15['bw_ema20']) and (prev_15['bandwidth'] <= prev_15['bw_ema20'])
                
                if daily_sqz and hourly_sqz and trigger_vol:
                    # Direction check 1: Upward Breakout
                    if curr_15['pct_b'] > 1.0:
                        status = "🚀 BUY BREAKOUT"
                        direction = "BULLISH"
                        atm_strike, otm_strike = calculate_spread_strikes(current_close, "BULLISH")
                        log_trigger_to_csv(stock_sym, stock_sec, current_close, atm_strike, otm_strike, selected_strategy, direction)
                    # Direction check 2: Downward Breakdown
                    elif curr_15['pct_b'] < 0.0:
                        status = "📉 SELL BREAKDOWN"
                        direction = "BEARISH"
                        atm_strike, otm_strike = calculate_spread_strikes(current_close, "BEARISH")
                        log_trigger_to_csv(stock_sym, stock_sec, current_close, atm_strike, otm_strike, selected_strategy, direction)
                elif daily_sqz and hourly_sqz:
                    status = "⚠️ WATCHLIST (Squeezed)"

            # --- EXECUTE LOGIC BLOCK B: MEAN REVERSAL ---
            elif selected_strategy == "Mean Reversal":
                bbm_col = f"BBM_{int(bb_len)}_{bb_std}"
                
                day_ob = df_day['rsi'].iloc[-1] >= rsi_ob or df_day['close'].iloc[-1] >= df_day[f"BBU_{int(bb_len)}_{bb_std}"].iloc[-1]
                day_os = df_day['rsi'].iloc[-1] <= rsi_os or df_day['close'].iloc[-1] <= df_day[f"BBL_{int(bb_len)}_{bb_std}"].iloc[-1]
                
                m_15 = df_15m.iloc[-1]
                p_15 = df_15m.iloc[-2]
                m15_cross_up = m_15['close'] > m_15[bbm_col] and p_15['close'] <= p_15[bbm_col]
                m15_cross_down = m_15['close'] < m_15[bbm_col] and p_15['close'] >= p_15[bbm_col]
                
                if day_os and m15_cross_up:
                    status = "🔥 BULLISH REVERSAL"
                    direction = "BULLISH"
                    atm_strike, otm_strike = calculate_spread_strikes(current_close, "BULLISH")
