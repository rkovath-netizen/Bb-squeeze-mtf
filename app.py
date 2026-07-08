import streamlit as st
import pandas as pd
import pandas_ta as ta
import upstox_client
from upstox_client.rest import ApiException
import os
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="FnO Momentum Engine", page_icon="🚀", layout="wide")
st.title("⚡ FnO 3-TF Momentum Engine & Automated Logger")

# --- LOAD SECURE ACCESS TOKEN ---
# Pulls the year-long analytics token directly from Streamlit Settings
if "UPSTOX_ANALYTICS_TOKEN" in st.secrets:
    access_token = st.secrets["UPSTOX_ANALYTICS_TOKEN"]
    st.sidebar.success("🔒 Upstox Analytics Token Connected!")
else:
    st.sidebar.error("❌ Missing Token! Add UPSTOX_ANALYTICS_TOKEN to Streamlit Secrets.")
    st.stop()

# Strategy Parameters
bb_len = st.sidebar.number_input("BB Length", 20)
bb_std = st.sidebar.number_input("BB StdDev", 2.0)
target_pct = st.sidebar.number_input("Target Upside % (For OTM Strike)", value=5.0)

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
    st.sidebar.warning(f"⚠️ {csv_filename} missing from repository root.")
    st.stop()

# --- STRIKE PRICE CALCULATOR ENGINE ---
def calculate_spread_strikes(current_price):
    price = float(current_price)
    
    # Standard Indian FnO step intervals based on stock price
    if price < 150:
        step = 2.5
    elif price < 500:
        step = 5.0
    elif price < 1500:
        step = 10.0
    elif price < 3000:
        step = 20.0
    else:
        step = 50.0
        
    atm_strike = round(price / step) * step
    target_price = price * (1 + (target_pct / 100))
    otm_strike = round(target_price / step) * step
    
    if otm_strike <= atm_strike:
        otm_strike = atm_strike + step
        
    return int(atm_strike), int(otm_strike)

# --- AUTOMATED FORWARD TEST LOGGER ---
def log_trigger_to_csv(symbol, sector, entry_price, atm_strike, otm_strike):
    now = datetime.now()
    log_data = {
        "Timestamp": [now.strftime("%Y-%m-%d %H:%M:%S")],
        "Date": [now.strftime("%Y-%m-%d")],
        "Symbol": [symbol],
        "Sector": [sector],
        "Entry_Price": [round(entry_price, 2)],
        "ATM_Buy_Call_Strike": [atm_strike],
        "OTM_Sell_Call_Strike": [otm_strike],
        "Status": ["OPEN"]
    }
    new_log_df = pd.DataFrame(log_data)
    
    if not os.path.exists(log_filename):
        new_log_df.to_csv(log_filename, index=False)
    else:
        # Check if symbol was already logged today to prevent duplicate rows on multiple clicks
        existing_logs = pd.read_csv(log_filename)
        today_str = now.strftime("%Y-%m-%d")
        duplicate = existing_logs[(existing_logs['Symbol'] == symbol) & (existing_logs['Date'] == today_str)]
        
        if duplicate.empty:
            new_log_df.to_csv(log_filename, mode='a', header=False, index=False)

# --- UPSTOX DATA QUERIES ---
# --- FETCH DATA FROM UPSTOX ---
def get_historical_data(symbol_name, interval):
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    
    # Calculate dates for the API request (Fetching last 100 days of data)
    to_date_str = datetime.now().strftime('%Y-%m-%d')
    from_date_str = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
    
    # Format the instrument key correctly
    inst_key = f"NSE_EQ|{str(symbol_name).strip().upper()}"
    
    try:
        # Use EXPLICIT keyword arguments to prevent positional errors
        api_response = api_instance.get_historical_candle_data1(
            instrument_key=inst_key,
            interval=interval,
            to_date=to_date_str,
            from_date=from_date_str,
            api_version="2.0"
        )
        
        if api_response.status == "success" and api_response.data.candles:
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(api_response.data.candles, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.iloc[::-1]  # Reverse to chronological order
            return df
            
    except ApiException as e:
        st.error(f"Upstox API Error for {symbol_name}: {e}")
        return None
        
    return pd.DataFrame()

def calculate_indicators(df):
    if df is None or df.empty: return df
    bb = ta.bbands(df['close'], length=bb_len, std=bb_std)
    df = pd.concat([df, bb], axis=1)
    
    bbl, bbu, bbm = f"BBL_{int(bb_len)}_{bb_std}", f"BBU_{int(bb_len)}_{bb_std}", f"BBM_{int(bb_len)}_{bb_std}"
    
    df['bandwidth'] = (df[bbu] - df[bbl]) / df[bbm]
    df['pct_b'] = (df['close'] - df[bbl]) / (df[bbu] - df[bbl])
    df['bw_sma50'] = ta.sma(df['bandwidth'], length=50)
    df['bw_ema20'] = ta.ema(df['bandwidth'], length=20)
    return df

# --- SCANNER RUN TIME ENGINE ---
if st.button("RUN SCANNER & LOG BREAKOUTS 🚀"):
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
            
            # Check strategy alignment conditions
            daily_sqz = df_day['bandwidth'].iloc[-1] < df_day['bw_sma50'].iloc[-1]
            hourly_sqz = df_1h['bandwidth'].iloc[-1] < df_1h['bw_sma50'].iloc[-1]
            
            curr_15 = df_15m.iloc[-1]
            prev_15 = df_15m.iloc[-2]
            trigger_price = curr_15['pct_b'] > 1.0
            trigger_vol = (curr_15['bandwidth'] > curr_15['bw_ema20']) and (prev_15['bandwidth'] <= prev_15['bw_ema20'])
            
            current_close = float(curr_15['close'])
            atm_buy, otm_sell = calculate_spread_strikes(current_close)
            
            if daily_sqz and hourly_sqz and trigger_price and trigger_vol:
                status = "🚀 BUY TRIGGER"
                log_trigger_to_csv(stock_sym, stock_sec, current_close, atm_buy, otm_sell)
            elif daily_sqz and hourly_sqz:
                status = "⚠️ WATCHLIST (Squeezed)"
            else:
                status = "❌ No Setup"
            
            results.append({
                "Symbol": stock_sym,
                "Sector": stock_sec,
                "Price": round(current_close, 2),
                "ATM Call (Buy)": atm_buy,
                "OTM Call (Sell)": otm_sell,
                "Status": status
            })
    
    # --- UI DISPLAY MATRIX ---
    if results:
        results_df = pd.DataFrame(results)
        triggers = results_df[results_df['Status'] == "🚀 BUY TRIGGER"]
        watchlist_only = results_df[results_df['Status'] == "⚠️ WATCHLIST (Squeezed)"]
        
        st.subheader("🔥 Active Breakout Triggers (Logged to CSV)")
        if not triggers.empty:
            st.success(f"Detected {len(triggers)} new trade entries!")
            st.dataframe(triggers[['Symbol', 'Sector', 'Price', 'ATM Call (Buy)', 'OTM Call (Sell)']])
        else:
            st.info("No active breakouts matching all 3-TF metrics right now.")
            
        st.subheader("⏳ Coiling Watchlist (Daily + Hourly Squeeze Active)")
        if not watchlist_only.empty:
            st.dataframe(watchlist_only[['Symbol', 'Sector', 'Price', 'ATM Call (Buy)', 'OTM Call (Sell)']])

# --- HISTORICAL FORWARD LOG VIEW ---
st.divider()
st.subheader("📁 Historical Forward-Testing Logs Matrix")
if os.path.exists(log_filename):
    try:
        history_df = pd.read_csv(log_filename)
        st.dataframe(history_df.sort_values(by="Timestamp", ascending=False))
        
        csv_data = history_df.to_csv(index=False).encode('utf-8')
        st.download_button(label="📥 Download Log File for Excel", data=csv_data, file_name="fno_forward_test_metrics.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Error rendering log view table: {str(e)}")
else:
    st.info("No forward test records logged yet. Run the scanner during market hours to capture active breakouts.")
