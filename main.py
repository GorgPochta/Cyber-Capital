import logging
import os
import requests
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "5860512200:AAE4tR8aVkpud3zldj1mV2z9jUJbhDKbQ8c"
RENDER_URL = "https://cyber-capital.onrender.com"
PORT = int(os.environ.get('PORT', 10000))
# =====================

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

def send_telegram(chat_id, text, keyboard=None):
    """Отправляет сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if keyboard:
        data['reply_markup'] = keyboard
    try:
        requests.post(url, json=data)
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")

def polling():
    """Постоянно проверяет новые сообщения"""
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            response = requests.get(url, params={'offset': offset, 'timeout': 30})
            data = response.json()
            
            if data['ok'] and data['result']:
                for update in data['result']:
                    offset = update['update_id'] + 1
                    
                    if 'message' in update:
                        msg = update['message']
                        chat_id = msg['chat']['id']
                        text = msg.get('text', '')
                        
                        logging.info(f"📨 От {chat_id}: {text}")
                        
                        if text == '/start':
                            # Кнопка для Mini App
                            keyboard = {
                                "inline_keyboard": [[
                                    {"text": "🚀 Открыть Monitor", "web_app": {"url": RENDER_URL}}
                                ]]
                            }
                            send_telegram(chat_id, 
                                f"👋 Привет! Твой Chat ID: <code>{chat_id}</code>",
                                keyboard)
        except Exception as e:
            logging.error(f"Ошибка polling: {e}")
        time.sleep(1)

# Запускаем polling в фоне
threading.Thread(target=polling, daemon=True).start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/healthcheck')
def health():
    return 'OK', 200

if __name__ == "__main__":
    # Сбрасываем вебхук
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    app.run(host='0.0.0.0', port=PORT)
