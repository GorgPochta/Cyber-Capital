import logging
import os
import asyncio
import requests
from datetime import datetime
import traceback

from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "5860512200:AAE4tR8aVkpud3zldj1mV2z9jUJbhDKbQ8c"
# Адрес твоего сервиса на Render (обязательно замени!)
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

# Глобальный объект приложения бота
bot_app = None
monitors = {}  # chat_id -> список мониторов

# ----- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (validate_symbol, PairMonitor и т.д.) -----
# ... (весь код классов PairMonitor и validate_symbol остается без изменений) ...
# Вставь сюда код для validate_symbol и класса PairMonitor из предыдущего сообщения
def validate_symbol(symbol):
    try:
        response = requests.get(
            f'https://api.bybit.com/v5/market/tickers',
            params={'category': 'linear', 'symbol': symbol.upper()},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data['retCode'] == 0 and len(data['result']['list']) > 0
    except:
        pass
    return False

class PairMonitor:
    def __init__(self, chat_id, pair_id, symbol1, symbol2, threshold, bot_app):
        self.chat_id = chat_id
        self.pair_id = pair_id
        self.symbol1 = symbol1.lower()
        self.symbol2 = symbol2.lower()
        self.threshold = threshold
        self.bot_app = bot_app
        self.active = True
        self.last_ratio = None
        self.thread = None
        self.running = True
        logging.info(f"✅ Создан монитор {symbol1}/{symbol2} для {chat_id}")
    
    def fetch_price(self, symbol):
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
    
    async def check(self):
        if not self.running:
            return
        
        try:
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
                        f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}"
                    )
                    try:
                        await self.bot_app.bot.send_message(
                            chat_id=self.chat_id,
                            text=signal,
                            parse_mode='HTML'
                        )
                        logging.info(f"✅ Сигнал отправлен")
                    except Exception as e:
                        logging.error(f"Ошибка отправки: {e}")
        except Exception as e:
            logging.error(f"Ошибка проверки: {traceback.format_exc()}")
        
        if self.running:
            import threading
            self.thread = threading.Timer(10, lambda: asyncio.run_coroutine_threadsafe(
                self.check(), self.bot_app.loop
            ))
            self.thread.start()
    
    def start(self):
        self.running = True
        import threading
        self.thread = threading.Timer(10, lambda: asyncio.run_coroutine_threadsafe(
            self.check(), self.bot_app.loop
        ))
        self.thread.start()
        logging.info(f"▶️ Запущен мониторинг {self.symbol1}/{self.symbol2}")
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.cancel()
        logging.info(f"⏹ Остановлен мониторинг {self.symbol1}/{self.symbol2}")
# ----- КОНЕЦ ВСПОМОГАТЕЛЬНЫХ ФУНКЦИЙ -----


# ----- FLASK ЭНДПОИНТЫ (ДЛЯ WEBAPP И WEBHOOK) -----
@app.route('/')
def index():
    return render_template('index.html')

# Эндпоинт для приема обновлений от Telegram (вебхук)
@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app:
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        asyncio.run_coroutine_threadsafe(bot_app.process_update(update), bot_app.loop)
    return '', 200

# Эндпоинт для проверки здоровья (Render его пингует)
@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return 'OK', 200

# ----- API ЭНДПОИНТЫ (те же, что и раньше) -----
@app.route('/api/pairs/<int:chat_id>')
def get_pairs(chat_id):
    try:
        user_pairs = []
        if chat_id in monitors:
            for p in monitors[chat_id]:
                user_pairs.append({
                    'id': p.pair_id,
                    'symbol1': p.symbol1,
                    'symbol2': p.symbol2,
                    'threshold': p.threshold,
                    'active': p.running,
                    'last_ratio': p.last_ratio
                })
        return jsonify({'pairs': user_pairs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/add_pair', methods=['POST'])
def add_pair():
    try:
        data = request.json
        chat_id = data.get('chatId')
        symbol1 = data.get('symbol1', '').lower().strip()
        symbol2 = data.get('symbol2', '').lower().strip()
        threshold = float(data.get('threshold', 0))
        
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
        monitor = PairMonitor(chat_id, pair_id, symbol1, symbol2, threshold, bot_app)
        monitors[chat_id].append(monitor)
        monitor.start()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/remove_pair', methods=['POST'])
def remove_pair():
    try:
        data = request.json
        chat_id = data.get('chatId')
        pair_id = data.get('pairId')
        
        if chat_id in monitors and 0 <= pair_id < len(monitors[chat_id]):
            monitors[chat_id][pair_id].stop()
            monitors[chat_id].pop(pair_id)
            for i, p in enumerate(monitors[chat_id]):
                p.pair_id = i
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    try:
        data = request.json
        chat_id = data.get('chatId')
        
        if chat_id in monitors:
            for p in monitors[chat_id]:
                p.stop()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ----- ОБРАБОТЧИКИ КОМАНД TELEGRAM -----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        keyboard = [[
            InlineKeyboardButton("🚀 Открыть Monitor", web_app=WebAppInfo(url=RENDER_URL))
        ]]
        keyboard.append([
            InlineKeyboardButton("📊 Мои пары", callback_data='list_pairs'),
            InlineKeyboardButton("⏹ Стоп все", callback_data='stop_all')
        ])
        
        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            f"Твой Chat ID: <code>{update.effective_chat.id}</code>\n\n"
            f"⬇️ Нажми кнопку ниже",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Start error: {traceback.format_exc()}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        
        if query.data == 'list_pairs':
            if chat_id in monitors and monitors[chat_id]:
                text = "📋 <b>Твои пары:</b>\n\n"
                for p in monitors[chat_id]:
                    status = "🟢" if p.running else "🔴"
                    last = f"{p.last_ratio:.6f}" if p.last_ratio else "—"
                    text += f"{status} {p.symbol1.upper()}/{p.symbol2.upper()}\n"
                    text += f"   Порог: {p.threshold} | Текущее: {last}\n\n"
                await query.edit_message_text(text, parse_mode='HTML')
            else:
                await query.edit_message_text("📭 Нет активных пар")
        
        elif query.data == 'stop_all':
            if chat_id in monitors:
                for p in monitors[chat_id]:
                    p.stop()
            await query.edit_message_text("⏹ Все мониторы остановлены")
            
    except Exception as e:
        logging.error(f"Button error: {traceback.format_exc()}")
        await query.message.reply_text("⚠️ Ошибка, но бот жив")

async def error_handler(update, context):
    logging.error(f"Ошибка: {context.error}")
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Произошла ошибка. Попробуй еще раз."
            )
    except:
        pass


# ----- ЗАПУСК -----
async def main():
    global bot_app
    logging.info("🚀 Запуск...")

    # Создаем приложение бота
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_error_handler(error_handler)

    # Инициализируем приложение
    await bot_app.initialize()

    # Устанавливаем вебхук
    webhook_url = f"{RENDER_URL}/webhook"
    await bot_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logging.info(f"✅ Вебхук установлен на {webhook_url}")

    # Запускаем Flask (это заблокирует поток, но бот будет жить в вебхуках)
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    asyncio.run(main())
