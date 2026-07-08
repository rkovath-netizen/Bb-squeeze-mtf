import os
import smtplib
import pandas as pd
import pandas_ta as ta
import upstox_client
from datetime import datetime, timedelta
from email.message import EmailMessage

# Credentials from environment variables
TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN")
GMAIL_USER = "9035490861r@gmail.com"
GMAIL_PASS = os.environ.get("GMAIL_PASSWORD")
LOG_FILE = "forward_test_log.csv"

def send_gmail_alert(symbol, signal_type, price):
    msg = EmailMessage()
    msg['Subject'] = f"Trade Alert: {signal_type} on {symbol}"
    msg['From'] = GMAIL_USER
    msg['To'] = GMAIL_USER
    msg.set_content(f"Signal Alert: {signal_type} for {symbol} at price {price}")
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.send_message(msg)

def get_15m_data(instrument_key):
    # Upstox only supports 1minute, 30minute, day, etc.
    api_instance = upstox_client.HistoryApi(upstox_client.ApiClient(upstox_client.Configuration(access_token=TOKEN)))
    to_date = datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    try:
        res = api_instance.get_historical_candle_data1(instrument_key, '1minute', to_date, from_date, '2.0')
        if res.status == 'success' and res.data.candles:
            df = pd.DataFrame(res.data.candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            # Resample 1m to 15m
            return df.resample('15T').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    except Exception:
        return None
    return None

def run_scanner():
    watchlist = pd.read_csv("fno_with_sectors.csv")
    for _, row in watchlist.iterrows():
        df = get_15m_data(row['instrument_key'])
        if df is None or len(df) < 3: continue
        
        # Logic: Previous candle closed, check signal
        prev_candle = df.iloc[-2]
        # Implement your strategy logic here
        # Example signal condition
        if prev_candle['close'] > prev_candle['open']: # Placeholder logic
            # Log to CSV
            new_entry = pd.DataFrame([[datetime.now(), row['Symbol'], "BUY", prev_candle['close']]], 
                                     columns=['Timestamp', 'Symbol', 'Signal', 'Price'])
            new_entry.to_csv(LOG_FILE, mode='a', header=not os.path.exists(LOG_FILE), index=False)
            send_gmail_alert(row['Symbol'], "BUY", prev_candle['close'])

if __name__ == "__main__":
    # Check market hours (09:30 - 15:30)
    now = datetime.now().time()
    if datetime.strptime("09:30", "%H:%M").time() <= now <= datetime.strptime("15:30", "%H:%M").time():
        run_scanner()
