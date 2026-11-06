import os
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Простейший HTTP-сервер для Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        # Минимальный ответ, чтобы cron-job.org не ругался на размер
        self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    # 1. Запускаем HTTP-сервер в отдельном фоновом потоке для Render
    threading.Thread(target=run_http_server, daemon=True).start()
    
    # 2. Запускаем вашего бота (bot.py)
    subprocess.run(["python", "bot.py"])

    # 3. Держим главный поток живым, чтобы сервер не выключался
    while True:
        time.sleep(1)
  
