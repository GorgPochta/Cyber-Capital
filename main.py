import logging
import os
import asyncio
import requests
from datetime import datetime, timedelta
import traceback
import threading
import time
import json

from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "5860512200:AAE4tR8aVkpud3zldj1mV2z9jUJbhDKbQ8c"
RENDER_URL = "https://cyber-capital.onrender.com"
PORT = int(os.environ.get('PORT', 10000))
# =====================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Создаем Flask приложение
app = Flask(__name__)

# Глобальные объекты
bot_app = None
monitors = {}  # chat_id -> список мониторов

# ===== ПРОВЕРКА ТИКЕРОВ =====
def validate_symbol(symbol):
    """Проверяет существование тикера на Bybit"""
    try:
        response = requests.get(
            f'https://api.bybit.com/v5/market/tickers',
            params={'category': 'linear', 'symbol': symbol.upper()},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data['retCode'] == 0 and len(data['result']['list']) > 0
    except Exception as e:
        logging.error(f"Ошибка проверки тикера {symbol}: {e}")
    return False

# ===== ФУНКЦИЯ ФОРМАТИРОВАНИЯ ИНТЕРВАЛА =====
def format_interval(value, unit):
    """Форматирует интервал для отображения"""
    names = {
        'minute': 'мин',
        'hour': 'ч',
        'day': 'дн',
        'week': 'нед',
        'month': 'мес'
    }
    if value == 1:
        if unit == 'minute': return '1 минуту'
        elif unit == 'hour': return '1 час'
        elif unit == 'day': return '1 день'
        elif unit == 'week': return '1 неделю'
        elif unit == 'month': return '1 месяц'
    else:
        if unit == 'minute': return f'{value} мин'
        elif unit == 'hour': return f'{value} ч'
        elif unit == 'day': return f'{value} дн'
        elif unit == 'week': return f'{value} нед'
        elif unit == 'month': return f'{value} мес'
    return f"{value} {names.get(unit, '')}"

# ===== ОБРАБОТЧИК КОМАНДЫ START =====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        f"Твой Chat ID: <code>{chat_id}</code>\n\n"
        f"⬇️ Используй кнопку в меню, чтобы открыть Mini App",
        parse_mode='HTML'
    )
    logging.info(f"✅ Команда /start от {chat_id}")

# ===== КЛАСС МОНИТОРА =====
class PairMonitor:
    def __init__(self, chat_id, pair_id, symbol1, symbol2, threshold, interval_value, interval_unit, bot_app):
        self.chat_id = chat_id
        self.pair_id = pair_id
        self.symbol1 = symbol1.lower()
        self.symbol2 = symbol2.lower()
        self.threshold = threshold
        self.interval_value = interval_value
        self.interval_unit = interval_unit
        self.bot_app = bot_app
        self.running = True
        self.last_ratio = None
        self.next_check = datetime.now()
        self.thread = None
        logging.info(f"✅ Создан монитор {symbol1}/{symbol2} для {chat_id}")
    
    def fetch_price(self, symbol):
        """Получает цену с Bybit"""
        try:
            response = requests.get(
                f'https://api.bybit.com/v5/market/tickers',
                params={'category': 'linear', 'symbol': symbol.upper()},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data['retCode'] == 0 and data['result']['list']:
                    return float(data['result']['list'][0]['lastPrice'])
        except Exception as e:
            logging.error(f"Ошибка получения цены {symbol}: {e}")
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
                    price1 = self.fetch_price(self.symbol1)
                    price2 = self.fetch_price(self.symbol2)
                    
                    if price1 and price2:
                        ratio = price1 / price2
                        self.last_ratio = ratio
                        logging.info(f"📊 {self.symbol1}/{self.symbol2} = {ratio:.6f}")
                        
                        if ratio >= self.threshold:
                            signal = (
                                f"🚨 <b>СИГНАЛ!</b>\n\n"
                                f"<b>Пара:</b> {self.symbol1.upper()}/{self.symbol2.upper()}\n"
                                f"<b>Отношение:</b> {ratio:.6f}\n"
                                f"<b>Порог:</b> {self.threshold}\n"
                                f"<b>Проверка:</b> {format_interval(self.interval_value, self.interval_unit)}\n"
                                f"<b>Время:</b> {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            )
                            
                            keyboard = {"inline_keyboard": [[
                                {"text": "⏸ Пауза", "callback_data": f"pause_{self.pair_id}"},
                                {"text": "⏹ Стоп", "callback_data": f"stop_{self.pair_id}"}
                            ]]}
                            
                            try:
                                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                                response = requests.post(url, json={
                                    "chat_id": self.chat_id,
                                    "text": signal,
                                    "parse_mode": "HTML",
                                    "reply_markup": keyboard
                                }, timeout=10)
                                if response.status_code == 200:
                                    logging.info(f"✅ Сигнал отправлен для пары {self.pair_id}")
                                else:
                                    logging.error(f"❌ Ошибка: {response.text}")
                            except Exception as e:
                                logging.error(f"❌ Ошибка отправки: {e}")
                    
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
        if self.thread:
            self.thread.join(timeout=1)
        logging.info(f"⏹ Остановлен {self.symbol1}/{self.symbol2}")
    
    def pause(self):
        """Ставит на паузу"""
        self.running = False
        logging.info(f"⏸ Пауза для {self.symbol1}/{self.symbol2}")

# ===== FLASK ЭНДПОИНТЫ =====
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/healthcheck')
def healthcheck():
    return 'OK', 200

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
        monitor = PairMonitor(chat_id, pair_id, symbol1, symbol2, threshold, 
                            interval_value, interval_unit, bot_app)
        monitors[chat_id].append(monitor)
        monitor.start()
        
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Ошибка: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/remove_pair', methods=['POST'])
def remove_pair():
    data = request.json
    chat_id = data.get('chatId')
    pair_id = data.get('pairId')
    if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
        monitors[chat_id][pair_id].stop()
        monitors[chat_id].pop(pair_id)
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

# ===== ОБРАБОТЧИКИ TELEGRAM =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton("🚀 Открыть Monitor", web_app=WebAppInfo(url=RENDER_URL))
    ]]
    keyboard.append([
        InlineKeyboardButton("📊 Мои пары", callback_data='list_pairs'),
        InlineKeyboardButton("⏹ Стоп все", callback_data='stop_all')
    ])
    await update.message.reply_text(
        f"👋 Привет, {update.effective_user.first_name}!\n\n⬇️ Нажми кнопку ниже",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    
    if query.data == 'list_pairs':
        if chat_id in monitors and monitors[chat_id]:
            text = "📋 <b>Твои пары:</b>\n\n"
            for p in monitors[chat_id]:
                text += f"{'🟢' if p.running else '🔴'} {p.symbol1.upper()}/{p.symbol2.upper()}\n"
                text += f"   🎯 {p.threshold} | ⏱ {format_interval(p.interval_value, p.interval_unit)}\n"
                text += f"   📊 {p.last_ratio or '—'}\n\n"
            await query.edit_message_text(text, parse_mode='HTML')
        else:
            await query.edit_message_text("📭 Нет активных пар")
    elif query.data == 'stop_all':
        if chat_id in monitors:
            for p in monitors[chat_id]:
                p.stop()
        await query.edit_message_text("⏹ Все мониторы остановлены")
    elif query.data.startswith('pause_'):
        pair_id = int(query.data.split('_')[1])
        if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
            monitors[chat_id][pair_id].pause()
            await query.edit_message_text("⏸ Пауза")
    elif query.data.startswith('stop_'):
        pair_id = int(query.data.split('_')[1])
        if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
            monitors[chat_id][pair_id].stop()
            await query.edit_message_text("⏹ Остановлен")

async def error_handler(update, context):
    logging.error(f"Ошибка: {context.error}")

# ===== ЗАПУСК =====
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def main():
    global bot_app
    logging.info("🚀 Запуск...")

    # Создаем приложение бота
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("start", start_command))  # Дублируем для надежности
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_error_handler(error_handler)

    # Инициализируем и запускаем бота
    await bot_app.initialize()
    await bot_app.start()
    
    # Устанавливаем вебхук
    webhook_url = f"{RENDER_URL}/webhook"
    await bot_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logging.info(f"✅ Вебхук установлен на {webhook_url}")

    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Держим приложение запущенным
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
