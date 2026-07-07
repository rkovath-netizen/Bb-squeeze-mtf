import streamlit as st
import pandas as pd
import pandas_ta as ta
import upstox_client
from upstox_client.rest import ApiException

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="3-TF Momentum Scanner", page_icon="⚡", layout="wide")
st.title("⚡ Momentum Engine: 3-TF Bollinger Squeeze")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("API & Strategy Settings")
# Use Streamlit Secrets (recommended) or paste temporary token
access_token = st.sidebar.text_input("Upstox Access Token", type="password")
symbol_code = st.sidebar.text_input("Instrument Token (e.g., NSE_EQ|INE002A01018)", "NSE_EQ|INE002A01018")

# Strategy Parameters
bb_len = st.sidebar.number_input("BB Length", 20)
bb_std = st.sidebar.number_input("BB StdDev", 2.0)
atr_period = st.sidebar.number_input("ATR Period", 14)

# --- HELPER FUNCTIONS ---
def get_historical_data(instrument_key, interval, api_version='2.0'):
    """
    Fetch candles from Upstox.
    Interval Map: 'day', '1h' (60min), '15m' (15min)
    """
    if not access_token:
        return None
    
    # Configure API
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(configuration))
    
    try:
        # Fetch last 100 candles to ensure enough data for EMA/BB
        api_response = api_instance.get_historical_candle_data1(instrument_key, interval, "100")
        
        # Parse Response
        if api_response.status == "success" and api_response.data.candles:
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = pd.DataFrame(api_response.data.candles, columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.iloc[::-1]  # Reverse to get chronological order
            return df
    except ApiException as e:
        st.error(f"API Error: {e}")
        return None
    return pd.DataFrame()

def calculate_indicators(df):
    if df.empty: return df
    
    # Calculate Bollinger Bands
    bb = ta.bbands(df['close'], length=bb_len, std=bb_std)
    df = pd.concat([df, bb], axis=1)
    
    # BB Columns (pandas_ta default naming: BBL_20_2.0, BBU_20_2.0, etc.)
    bbl = f"BBL_{int(bb_len)}_{bb_std}"
    bbu = f"BBU_{int(bb_len)}_{bb_std}"
    bbm = f"BBM_{int(bb_len)}_{bb_std}"
    
    # Bandwidth & %B
    df['bandwidth'] = (df[bbu] - df[bbl]) / df[bbm]
    df['pct_b'] = (df['close'] - df[bbl]) / (df[bbu] - df[bbl])
    
    # Bandwidth 50-SMA (for Squeeze baseline) & 20-EMA (for expansion)
    df['bw_sma50'] = ta.sma(df['bandwidth'], length=50)
    df['bw_ema20'] = ta.ema(df['bandwidth'], length=20)
    
    return df

# --- MAIN EXECUTION ---
if st.button("RUN SCANNER 🚀"):
    with st.spinner("Fetching data from Upstox..."):
        # 1. Fetch Data
        df_day = get_historical_data(symbol_code, "day")
        df_1h = get_historical_data(symbol_code, "60minute")
        df_15m = get_historical_data(symbol_code, "15minute")
        
        if df_day is not None and not df_day.empty:
            # 2. Process Indicators
            df_day = calculate_indicators(df_day)
            df_1h = calculate_indicators(df_1h)
            df_15m = calculate_indicators(df_15m)
            
            # 3. Check Logic
            # Last completed candle (iloc[-1] is current/forming, iloc[-2] is last closed)
            # Adjust index [-1] vs [-2] based on whether you want confirmed or live signals
            
            # A. Daily Squeeze: Bandwidth < 50-SMA
            daily_sqz = df_day['bandwidth'].iloc[-1] < df_day['bw_sma50'].iloc[-1]
            
            # B. Hourly Squeeze: Bandwidth < 50-SMA
            hourly_sqz = df_1h['bandwidth'].iloc[-1] < df_1h['bw_sma50'].iloc[-1]
            
            # C. 15m Trigger: Price > Upper Band AND Bandwidth expanding (Crossed 20 EMA)
            curr_15 = df_15m.iloc[-1]
            prev_15 = df_15m.iloc[-2]
            
            trigger_price = curr_15['pct_b'] > 1.0
            trigger_vol = (curr_15['bandwidth'] > curr_15['bw_ema20']) and \
                          (prev_15['bandwidth'] <= prev_15['bw_ema20'])
            
            # --- DASHBOARD ---
            col1, col2, col3 = st.columns(3)
            
            col1.metric("Daily Squeeze", "ACTIVE" if daily_sqz else "Inactive", 
                        delta_color="normal" if daily_sqz else "off")
            
            col2.metric("Hourly Squeeze", "ACTIVE" if hourly_sqz else "Inactive", 
                        delta_color="normal" if hourly_sqz else "off")
            
            state_color = "normal" if (trigger_price and trigger_vol) else "off"
            col3.metric("15m Trigger", "FIRED 🔥" if (trigger_price and trigger_vol) else "Waiting", 
                        delta_color=state_color)
            
            st.divider()
            
            # FINAL SIGNAL
            if daily_sqz and hourly_sqz and trigger_price and trigger_vol:
                st.success("## 🚀 BUY SIGNAL CONFIRMED")
                st.write("Review Option Chain for Bull Call Spread entry.")
                # Play sound logic or send Telegram msg here
            elif daily_sqz and hourly_sqz:
                st.warning("⚠️ WATCHLIST: Squeeze aligned. Waiting for 15m Trigger.")
            else:
                st.info("Market Status: No Setup.")
                
            # Debug Data
            with st.expander("View Raw Data"):
                st.write("15 Min Data", df_15m.tail())
