"""
Polymarket BTC Up/Down 15m — Bot direccional
Uso: python main.py
"""
import sys

# Salida en UTF-8 siempre (evita crash con caracteres Unicode en consolas cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from strategy import run

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nBot detenido por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR FATAL] {e}")
        raise
