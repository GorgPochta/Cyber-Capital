import main
import threading
import requests
import logging

logging.basicConfig(level=logging.INFO)

# Запускаем polling
threading.Thread(target=main.polling, daemon=True).start()
logging.info("✅ Polling запущен из app.py")

# Запускаем Flask
main.app.run(host='0.0.0.0', port=main.PORT)
