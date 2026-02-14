import requests
import threading
import time
from datetime import datetime
import asyncio

class PriceMonitor:
    def __init__(self, bot_app):
        self.bot_app = bot_app
        self.active_monitors = {}  # chat_id -> {pairs: [...]}
        self.prices = {}  # cache цен
        
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
            print(f"Ошибка получения цены {symbol}: {e}")
        return None
    
    def get_pair_price(self, symbol1, symbol2):
        """Получает отношение двух цен"""
        price1 = self.fetch_price(symbol1)
        price2 = self.fetch_price(symbol2)
        
        if price1 and price2:
            return price1 / price2
        return None
    
    async def check_pair(self, chat_id, pair_config):
        """Проверяет одну пару"""
        if not pair_config['active']:
            return
            
        ratio = self.get_pair_price(pair_config['symbol1'], pair_config['symbol2'])
        
        if ratio:
            current_time = datetime.now().strftime('%H:%M:%S')
            pair_name = f"{pair_config['symbol1'].upper()}/{pair_config['symbol2'].upper()}"
            
            # Проверяем сигнал
            if ratio >= pair_config['threshold']:
                signal_msg = (
                    f"🚨 <b>СИГНАЛ!</b>\n\n"
                    f"<b>Пара:</b> {pair_name}\n"
                    f"<b>Отношение:</b> {ratio:.6f}\n"
                    f"<b>Порог:</b> {pair_config['threshold']}\n"
                    f"<b>Время:</b> {current_time}"
                )
                try:
                    await self.bot_app.bot.send_message(
                        chat_id=chat_id,
                        text=signal_msg,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    print(f"Ошибка отправки: {e}")
            
            # Обновляем последнее значение
            pair_config['last_ratio'] = ratio
            pair_config['last_check'] = current_time
    
    async def check_all_pairs(self):
        """Проверяет все активные пары всех пользователей"""
        for chat_id, user_data in self.active_monitors.items():
            for pair in user_data.get('pairs', []):
                if pair['active']:
                    await self.check_pair(chat_id, pair)
        
        # Запускаем следующую проверку через 10 секунд
        threading.Timer(10, lambda: asyncio.run_coroutine_threadsafe(
            self.check_all_pairs(), self.bot_app.loop
        )).start()
    
    def add_pair(self, chat_id, symbol1, symbol2, threshold):
        """Добавляет пару для мониторинга"""
        if chat_id not in self.active_monitors:
            self.active_monitors[chat_id] = {'pairs': []}
        
        new_pair = {
            'id': len(self.active_monitors[chat_id]['pairs']),
            'symbol1': symbol1.lower(),
            'symbol2': symbol2.lower(),
            'threshold': threshold,
            'active': True,
            'last_ratio': None,
            'last_check': None,
            'created': datetime.now().isoformat()
        }
        
        self.active_monitors[chat_id]['pairs'].append(new_pair)
        return new_pair
    
    def remove_pair(self, chat_id, pair_id):
        """Удаляет пару"""
        if chat_id in self.active_monitors:
            pairs = self.active_monitors[chat_id]['pairs']
            self.active_monitors[chat_id]['pairs'] = [p for p in pairs if p['id'] != pair_id]
    
    def stop_all(self, chat_id):
        """Останавливает все пары пользователя"""
        if chat_id in self.active_monitors:
            for pair in self.active_monitors[chat_id]['pairs']:
                pair['active'] = False
    
    def get_user_pairs(self, chat_id):
        """Возвращает список пар пользователя"""
        if chat_id in self.active_monitors:
            return self.active_monitors[chat_id]['pairs']
        return []