"""
Polymarket BTC Up/Down 15m — Arbitrage Bot
Uso: python main.py
"""
import sys
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
