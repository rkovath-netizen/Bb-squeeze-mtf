import smtplib
from email.message import EmailMessage
import os

def send_gmail_alert(symbol, signal_type):
    msg = EmailMessage()
    msg.set_content(f"Signal Alert: {signal_type} on {symbol}")
    msg['Subject'] = f"Trade Alert: {symbol}"
    msg['From'] = "9035490861r@gmail.com"
    msg['To'] = "9035490861r@gmail.com"

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login("9035490861r@gmail.com", os.environ.get("GMAIL_PASSWORD"))
        smtp.send_message(msg)
