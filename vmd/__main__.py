"""Run the local Flask UI with python -m vmd."""
from .app import create_app

if __name__ == "__main__":
    print("VMD Shield: http://127.0.0.1:8765", flush=True)
    app = create_app()
    app.run(host="127.0.0.1", port=8765, threaded=True, use_reloader=False)
