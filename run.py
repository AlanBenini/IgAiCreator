import os
import socket
import threading
import time
import webbrowser

import uvicorn


def find_port() -> int:
    requested_port = int(os.getenv("PORT", "8000"))
    for port in range(requested_port, requested_port + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("Nessuna porta disponibile tra 8000 e 8009.")


def open_browser(port: int):
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}/?v={int(time.time())}")


if __name__ == "__main__":
    port = find_port()
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    print(f"MOMENT avviato su http://127.0.0.1:{port}")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False)
