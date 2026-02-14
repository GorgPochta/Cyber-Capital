import logging
import os
import requests
import threading
import time
import json
import traceback
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "5860512200:AAE4tR8aVkpud3zldj1mV2z9jUJbhDKbQ8c"
RENDER_URL = "https://cyber-capital.onrender.com"
PORT = int(os.environ.get('PORT', 10000))
# =====================

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# Хранилище мониторов
monitors = {}

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def validate_symbol(symbol):
    """Проверяет существование тикера на Bybit"""
    try:
        response = requests.get(f'https://api.bybit.com/v5/market/tickers', 
                               params={'category': 'linear', 'symbol': symbol.upper()}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data['retCode'] == 0 and len(data['result']['list']) > 0
    except: 
        return False
    return False

def format_interval(value, unit):
    """Форматирует интервал для отображения"""
    names = {'minute': 'мин', 'hour': 'ч', 'day': 'дн', 'week': 'нед', 'month': 'мес'}
    if value == 1:
        return {'minute': '1 минуту', 'hour': '1 час', 'day': '1 день', 
                'week': '1 неделю', 'month': '1 месяц'}.get(unit, f'1 {names[unit]}')
    return f'{value} {names.get(unit, "")}'

def send_telegram(chat_id, text, keyboard=None):
    """Отправляет сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if keyboard:
        data['reply_markup'] = json.dumps(keyboard)
    try:
        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200:
            logging.info(f"✅ Сообщение отправлено в чат {chat_id}")
            return True
        else:
            logging.error(f"❌ Ошибка Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")
        return False

# ===== КЛАСС МОНИТОРИНГА =====
class PairMonitor:
    def __init__(self, chat_id, pair_id, symbol1, symbol2, threshold, interval_value, interval_unit):
        self.chat_id = chat_id
        self.pair_id = pair_id
        self.symbol1 = symbol1.lower()
        self.symbol2 = symbol2.lower()
        self.threshold = threshold
        self.interval_value = interval_value
        self.interval_unit = interval_unit
        self.running = True
        self.last_ratio = None
        self.next_check = datetime.now()
        self.thread = None
        logging.info(f"✅ Создан монитор {symbol1}/{symbol2} для {chat_id}")
    
    def fetch_price(self, symbol):
        """Получает цену с Bybit"""
        try:
            response = requests.get(f'https://api.bybit.com/v5/market/tickers',
                                   params={'category': 'linear', 'symbol': symbol.upper()}, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data['retCode'] == 0 and data['result']['list']:
                    return float(data['result']['list'][0]['lastPrice'])
        except: 
            pass
        return None
    
    def get_next_check(self):
        """Вычисляет время следующей проверки"""
        now = datetime.now()
        if self.interval_unit == 'minute':
            return now + timedelta(minutes=self.interval_value)
        elif self.interval_unit == 'hour':
            return now + timedelta(hours=self.interval_value)
        elif self.interval_unit == 'day':
            return now + timedelta(days=self.interval_value)
        elif self.interval_unit == 'week':
            return now + timedelta(weeks=self.interval_value)
        elif self.interval_unit == 'month':
            return now + timedelta(days=30 * self.interval_value)
        return now + timedelta(hours=1)
    
    def check_loop(self):
        """Основной цикл проверки"""
        while self.running:
            try:
                now = datetime.now()
                if now >= self.next_check:
                    # Получаем цены
                    price1 = self.fetch_price(self.symbol1)
                    price2 = self.fetch_price(self.symbol2)
                    
                    if price1 and price2:
                        ratio = price1 / price2
                        self.last_ratio = ratio
                        
                        logging.info(f"📊 {self.symbol1}/{self.symbol2} = {ratio:.6f}")
                        
                        # Проверяем порог
                        if ratio >= self.threshold:
                            logging.info(f"🎯 СРАБОТАЛО! {ratio:.6f} >= {self.threshold}")
                            
                            # Формируем сообщение
                            signal = (
                                f"🚨 <b>СИГНАЛ!</b>\n\n"
                                f"<b>Пара:</b> {self.symbol1.upper()}/{self.symbol2.upper()}\n"
                                f"<b>Отношение:</b> {ratio:.6f}\n"
                                f"<b>Порог:</b> {self.threshold}\n"
                                f"<b>Интервал:</b> {format_interval(self.interval_value, self.interval_unit)}\n"
                                f"<b>Время:</b> {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            )
                            
                            # Кнопки управления
                            keyboard = {
                                "inline_keyboard": [[
                                    {"text": "⏸ Пауза", "callback_data": f"pause_{self.pair_id}"},
                                    {"text": "⏹ Стоп", "callback_data": f"stop_{self.pair_id}"}
                                ]]
                            }
                            
                            # ОТПРАВЛЯЕМ В ТЕЛЕГРАМ
                            send_telegram(self.chat_id, signal, keyboard)
                    
                    # Устанавливаем следующую проверку
                    self.next_check = self.get_next_check()
                
                time.sleep(5)
            except Exception as e:
                logging.error(f"Ошибка в цикле: {traceback.format_exc()}")
                time.sleep(10)
    
    def start(self):
        """Запускает мониторинг"""
        self.running = True
        self.next_check = self.get_next_check()
        self.thread = threading.Thread(target=self.check_loop)
        self.thread.daemon = True
        self.thread.start()
        logging.info(f"▶️ Запущен {self.symbol1}/{self.symbol2}")
    
    def stop(self):
        """Останавливает мониторинг"""
        self.running = False
        logging.info(f"⏹ Остановлен {self.symbol1}/{self.symbol2}")
    
    def pause(self):
        """Ставит на паузу"""
        self.running = False
        logging.info(f"⏸ Пауза для {self.symbol1}/{self.symbol2}")

# ===== API ЭНДПОИНТЫ =====
@app.route('/api/pairs/<int:chat_id>')
def get_pairs(chat_id):
    pairs = []
    if chat_id in monitors:
        for p in monitors[chat_id]:
            pairs.append({
                'id': p.pair_id,
                'symbol1': p.symbol1,
                'symbol2': p.symbol2,
                'threshold': p.threshold,
                'interval_value': p.interval_value,
                'interval_unit': p.interval_unit,
                'active': p.running,
                'last_ratio': p.last_ratio
            })
    return jsonify({'pairs': pairs})

@app.route('/api/add_pair', methods=['POST'])
def add_pair():
    try:
        data = request.json
        chat_id = data.get('chatId')
        symbol1 = data.get('symbol1', '').lower().strip()
        symbol2 = data.get('symbol2', '').lower().strip()
        threshold = float(data.get('threshold', 0))
        interval_value = int(data.get('interval_value', 1))
        interval_unit = data.get('interval_unit', 'day')
        
        # Валидация
        if not all([chat_id, symbol1, symbol2, threshold]):
            return jsonify({'error': 'Заполни все поля'}), 400
        
        if not symbol1.endswith('usdt') or not symbol2.endswith('usdt'):
            return jsonify({'error': 'Тикеры должны заканчиваться на usdt'}), 400
        
        if not validate_symbol(symbol1):
            return jsonify({'error': f'Тикер {symbol1} не найден'}), 400
        if not validate_symbol(symbol2):
            return jsonify({'error': f'Тикер {symbol2} не найден'}), 400
        
        if chat_id not in monitors:
            monitors[chat_id] = []
        
        pair_id = len(monitors[chat_id])
        monitor = PairMonitor(chat_id, pair_id, symbol1, symbol2, threshold, interval_value, interval_unit)
        monitors[chat_id].append(monitor)
        monitor.start()
        
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Ошибка добавления: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/remove_pair', methods=['POST'])
def remove_pair():
    data = request.json
    chat_id = data.get('chatId')
    pair_id = data.get('pairId')
    if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
        monitors[chat_id][pair_id].stop()
        monitors[chat_id].pop(pair_id)
        # Обновляем ID
        for i, p in enumerate(monitors[chat_id]):
            p.pair_id = i
    return jsonify({'success': True})

@app.route('/api/toggle_pair', methods=['POST'])
def toggle_pair():
    data = request.json
    chat_id = data.get('chatId')
    pair_id = data.get('pairId')
    if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
        m = monitors[chat_id][pair_id]
        if m.running:
            m.pause()
        else:
            m.running = True
            m.start()
    return jsonify({'success': True})

@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    data = request.json
    chat_id = data.get('chatId')
    if chat_id in monitors:
        for p in monitors[chat_id]:
            p.stop()
    return jsonify({'success': True})

@app.route('/api/log_chat', methods=['POST'])
def log_chat():
    data = request.json
    chat_id = data.get('chatId')
    logging.info(f"📱 WebApp передал Chat ID: {chat_id}")
    return jsonify({'ok': True})

# ===== ОСНОВНЫЕ СТРАНИЦЫ =====
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/healthcheck')
def health():
    return 'OK', 200

# ===== ОБРАБОТЧИК КОМАНД TELEGRAM =====
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
                    
                    # Обработка обычных сообщений
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
                                f"👋 Привет! Твой Chat ID: <code>{chat_id}</code>\n\nНажми кнопку ниже, чтобы открыть Mini App.",
                                keyboard)
                    
                    # Обработка нажатий на кнопки
                    if 'callback_query' in update:
                        cb = update['callback_query']
                        chat_id = cb['message']['chat']['id']
                        data = cb['data']
                        
                        if data.startswith('pause_'):
                            pair_id = int(data.split('_')[1])
                            if chat_id in monitors and pair_id < len(monitors[chat_id]):
                                monitors[chat_id][pair_id].pause()
                                send_telegram(chat_id, "⏸ Пауза")
                        
                        elif data.startswith('stop_'):
                            pair_id = int(data.split('_')[1])
                            if chat_id in monitors and pair_id < len(monitors[chat_id]):
                                monitors[chat_id][pair_id].stop()
                                send_telegram(chat_id, "⏹ Остановлен")
                        
                        # Отвечаем на callback
                        callback_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
                        requests.post(callback_url, json={'callback_query_id': cb['id']})
                        
        except Exception as e:
            logging.error(f"Ошибка polling: {e}")
        time.sleep(1)

# ===== ЗАПУСК =====
if __name__ == "__main__":
    # Сбрасываем вебхук
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    logging.info("✅ Вебхук сброшен")
    
    # Запускаем polling в фоне
    threading.Thread(target=polling, daemon=True).start()
    logging.info("✅ Telegram polling запущен")
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=PORT)
