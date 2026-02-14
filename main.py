import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask, render_template, request, jsonify
import threading
import json
import os
from monitor import PriceMonitor

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "5860512200:AAE4tR8aVkpud3zldj1mV2z9jUJbhDKbQ8c"
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
monitor = None

# ===== FLASK (WEBAPP) =====
@app.route('/')
def index():
    """Главная страница Mini App"""
    return render_template('index.html')

@app.route('/api/pairs/<int:chat_id>')
def get_pairs(chat_id):
    """API для получения списка пар пользователя"""
    if monitor:
        pairs = monitor.get_user_pairs(chat_id)
        return jsonify({'pairs': pairs})
    return jsonify({'pairs': []})

@app.route('/api/add_pair', methods=['POST'])
def add_pair():
    """API для добавления пары"""
    data = request.json
    chat_id = data.get('chat_id')
    symbol1 = data.get('symbol1')
    symbol2 = data.get('symbol2')
    threshold = data.get('threshold')
    
    if monitor and chat_id:
        pair = monitor.add_pair(chat_id, symbol1, symbol2, threshold)
        return jsonify({'success': True, 'pair': pair})
    return jsonify({'success': False})

@app.route('/api/remove_pair', methods=['POST'])
def remove_pair():
    """API для удаления пары"""
    data = request.json
    chat_id = data.get('chat_id')
    pair_id = data.get('pair_id')
    
    if monitor and chat_id:
        monitor.remove_pair(chat_id, pair_id)
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    """API для остановки всех пар"""
    data = request.json
    chat_id = data.get('chat_id')
    
    if monitor and chat_id:
        monitor.stop_all(chat_id)
        return jsonify({'success': True})
    return jsonify({'success': False})

# ===== TELEGRAM БОТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - отправляет кнопку для открытия Mini App"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Кнопка для открытия WebApp
    keyboard = [[
        InlineKeyboardButton(
            "🚀 Открыть Crypto Monitor", 
            web_app=WebAppInfo(url="https://твой-сервис.onrender.com")
        )
    ]]
    
    # Добавляем кнопку возврата в меню
    keyboard.append([
        InlineKeyboardButton("📊 Мои пары", callback_data='list_pairs'),
        InlineKeyboardButton("⏹ Остановить все", callback_data='stop_all')
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        f"🎮 Нажми кнопку ниже, чтобы открыть Mini App и настроить мониторинг.\n\n"
        f"Твой Chat ID: <code>{chat_id}</code> (сохрани его)",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    if query.data == 'list_pairs':
        if monitor:
            pairs = monitor.get_user_pairs(chat_id)
            if pairs:
                text = "📋 <b>Твои пары:</b>\n\n"
                for p in pairs:
                    status = "🟢" if p['active'] else "🔴"
                    text += f"{status} {p['symbol1'].upper()}/{p['symbol2'].upper()}\n"
                    text += f"   Порог: {p['threshold']}\n"
                    text += f"   Последнее: {p['last_ratio'] or '—'}\n\n"
                await query.edit_message_text(text, parse_mode='HTML')
            else:
                await query.edit_message_text("📭 У тебя пока нет сохраненных пар.")
    
    elif query.data == 'stop_all':
        if monitor:
            monitor.stop_all(chat_id)
            await query.edit_message_text("⏹ Все мониторы остановлены.")

async def post_init(application):
    """Вызывается после инициализации бота"""
    global bot_app, monitor
    bot_app = application
    monitor = PriceMonitor(application)
    
    # Запускаем мониторинг
    await monitor.check_all_pairs()
    print("✅ Мониторинг запущен")

def run_flask():
    """Запускает Flask сервер"""
    app.run(host='0.0.0.0', port=10000)

def main():
    """Запуск бота и Flask"""
    print("🚀 Запуск Crypto Monitor...")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Создаем Telegram бота
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("✅ Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()