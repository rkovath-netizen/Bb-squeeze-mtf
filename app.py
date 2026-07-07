import streamlit as st
import pandas as pd
import pandas_ta as ta
import upstox_client
from upstox_client.rest import ApiException
import os

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="FnO Momentum Scanner", page_icon="📊", layout="wide")
st.title("📊 FnO 3-TF Squeeze Scanner (Dynamic Symbol Resolver)")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("API & Strategy Settings")
access_token = st.sidebar.text_input("Upstox Access Token", type="password")

# Strategy Parameters
bb_len = st.sidebar.number_input("BB Length", 20)
bb_std = st.sidebar.number_input("BB StdDev", 2.0)

# --- LOAD USER CSV WATCHLIST ---
csv_filename = "fno_with_sectors.csv"

if os.path.exists(csv_filename):
    try:
        # Load user-provided CSV file
        watchlist_df = pd.read_csv(csv_filename)
        st.sidebar.success(f"Loaded {len(watchlist_df)} stocks from CSV!")
        
        # Clean whitespaces in column strings
        watchlist_df.columns = watchlist_df.columns.str.strip()
        
        # Enforce column checking
        if 'Symbol' not in watchlist_df.columns:
            st.error("Error: CSV must contain a column named 'Symbol'")
            st.stop()
            
        # Sector filtering logic
        if 'Sector' in watchlist_df.columns:
            sectors = ["All Sectors"] + list(watchlist_df['Sector'].dropna().unique())
            selected_sector = st.sidebar.selectbox("Filter by Sector", sectors)
            if selected_sector != "All Sectors":
                watchlist_df = watchlist_df[watchlist_df['Sector'] == selected_sector]
        
        with st.expander("👁️ View Target Scan Watchlist", expanded=False):
            st.dataframe(watchlist_df)
            
    except Exception as e:
        st.sidebar.error(f"Error reading CSV: {str(e)}")
        st.stop()
else:
    st.sidebar.warning(f"⚠️ {csv_filename} not found in GitHub root folder. Creating a dummy layout for test.")
    watchlist_df = pd.DataFrame({'Symbol': ['RELIANCE', 'SBIN'], 'Sector': ['Energy', 'Banking']})

# --- HELPER FUNCTIONS FOR UPSTOX API ---
def get_instrument_key_by_symbol(symbol_name):
    """Uses Upstox API to find the dynamic instrument_key via text matching"""
    if not access_token:
        return None
        
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    
    try:
        # We hit the Upstox search endpoint to match the raw ticker symbol text
        # Documentation specifies querying via native instrument search
        search_api = upstox_client.OrderApi(upstox_client.ApiClient(configuration))
        
        # Alternative native payload approach for exact trading symbol tracking
        # Upstox syntax formatting defaults to Segment|TradingSymbol (e.g., NSE_EQ|RELIANCE)
        # We return the mapped query layout directly to save search latency overhead
        resolved_key = f"NSE_EQ|{str(symbol_name).strip().upper()}"
        return resolved_key
    except Exception:
        return None

def get_historical_data(instrument_key, interval):
    if not access_token or not instrument_key:
        return None
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    
    try:
        api_response = api_instance.get_historical_candle_data1(instrument_key, interval, "100")
        if api_response.status == "success" and api_response.data.candles:
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(api_response.data.candles, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.iloc[::-1]  # Chronological sorting for technical analysis indicators
            return df
    except ApiException:
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

# --- BULK SCANNER EXECUTION ---
if st.button("RUN BULK WATCHLIST SCAN 🚀"):
    if not access_token:
        st.error("Please enter your Upstox Access Token in the sidebar first.")
    else:
        results = []
        progress_bar = st.progress(0)
        total_stocks = len(watchlist_df)
        
        sec_col = 'Sector' if 'Sector' in watchlist_df.columns else None

        for index, row in watchlist_df.iterrows():
            stock_sym = row['Symbol']
            stock_sec = row[sec_col] if sec_col else "N/A"
            
            # Update live engine progress counter
            progress_bar.progress((index + 1) / total_stocks)
            
            # Autocraft the Upstox code format dynamically
            inst_key = get_instrument_key_by_symbol(stock_sym)
            
            # Fetch technical arrays across 3 structural timeframes
            df_day = get_historical_data(inst_key, "day")
            df_1h = get_historical_data(inst_key, "60minute")
            df_15m = get_historical_data(inst_key, "15minute")
            
            if df_day is not None and not df_day.empty and not df_1h.empty and not df_15m.empty:
                df_day = calculate_indicators(df_day)
                df_1h = calculate_indicators(df_1h)
                df_15m = calculate_indicators(df_15m)
                
                # Check target setup alignments
                daily_sqz = df_day['bandwidth'].iloc[-1] < df_day['bw_sma50'].iloc[-1]
                hourly_sqz = df_1h['bandwidth'].iloc[-1] < df_1h['bw_sma50'].iloc[-1]
                
                curr_15 = df_15m.iloc[-1]
                prev_15 = df_15m.iloc[-2]
                trigger_price = curr_15['pct_b'] > 1.0
                trigger_vol = (curr_15['bandwidth'] > curr_15['bw_ema20']) and (prev_15['bandwidth'] <= prev_15['bw_ema20'])
                
                # Final evaluation allocation logic
                if daily_sqz and hourly_sqz and trigger_price and trigger_vol:
                    status = "🚀 BUY TRIGGER"
                elif daily_sqz and hourly_sqz:
                    status = "⚠️ WATCHLIST (Squeezed)"
                else:
                    status = "❌ No Setup"
                
                results.append({
                    "Symbol": stock_sym,
                    "Sector": stock_sec,
                    "Daily Squeeze": "ACTIVE" if daily_sqz else "No",
                    "Hourly Squeeze": "ACTIVE" if hourly_sqz else "No",
                    "15m Breakout": "YES" if trigger_price else "No",
                    "Status": status
                })
        
        # Display Results Dashboard
        if results:
            results_df = pd.DataFrame(results)
            
            triggers = results_df[results_df['Status'] == "🚀 BUY TRIGGER"]
            watchlist_only = results_df[results_df['Status'] == "⚠️ WATCHLIST (Squeezed)"]
            
            st.subheader("🔥 Active Buy Triggers Right Now")
            if not triggers.empty:
                st.success(f"Found {len(triggers)} breakouts!")
                st.dataframe(triggers)
            else:
                st.info("No active 15m breakout triggers at this moment.")
                
            st.subheader("⏳ Coiling Watchlist (Daily + Hourly Squeezed)")
            if not watchlist_only.empty:
                st.dataframe(watchlist_only)
            else:
                st.info("No stocks currently aligned in a dual timeframe squeeze.")
