"""
Brain v2 — Estrategia ganadora basada en dos edges reales:

EDGE 1 — Apertura de ventana (T=0-90s)
  Chainlink fijó el precio de apertura. Binance YA se movió.
  El mercado empieza en ~50/50 por defecto.
  Si spot está $THRESHOLD alejado del precio de apertura → entrar el lado ganador a ~50¢.

EDGE 2 — Lag de oráculo Chainlink (T=3-10min)
  Chainlink actualiza cada ~27s. Entre actualizaciones el mercado no refleja
  el spot real. Si spot confirma la dirección Chainlink Y el mercado sigue
  subvalorando ese lado → entrar.

FILTROS de seguridad:
  - Nunca entrar contra el mercado cuando precio > 75¢ (el mercado ya sabe)
  - Chainlink y Binance deben coincidir en dirección
  - Movimiento mínimo confirmado ($MIN_MOVE)
"""
import json
import math
import os
from dataclasses import dataclass

STATS_FILE = os.path.join(os.path.dirname(__file__), "brain_stats.json")

# ── Parámetros configurables ──────────────────────────────────────────────────
MIN_MOVE_OPEN    = 40.0   # $ mínimo de movimiento en T=0-90s para entrar en apertura
MIN_MOVE_MID     = 80.0   # $ mínimo de movimiento confirmado para entrar a mitad
MAX_ENTRY_PRICE  = 0.72   # nunca pagar más de 72¢ (payout mínimo aceptable ~39%)
MIN_ENTRY_PRICE  = 0.42   # nunca pagar menos de 42¢ (señal demasiado tarde/obvia)
EDGE_THRESHOLD   = 0.08   # ventaja mínima modelo vs mercado para entrar
OPEN_WINDOW_SECS = 120    # segundos iniciales donde aplica Edge 1


@dataclass
class Signal:
    side:         str    # "up" o "down"
    edge_type:    str    # "open_window" | "oracle_lag" | "limit_40"
    market_price: float
    p_true:       float
    edge:         float
    btc_diff:     float  # chainlink_now - chainlink_open
    spot_diff:    float  # binance_now - binance_open
    secs_elapsed: float
    secs_left:    float


class Brain:
    def __init__(self):
        self.vol_per_sec: float  = 0.55    # σ BTC en $/segundo (se actualiza en vivo)
        self.edge_threshold: float = EDGE_THRESHOLD
        self.history: list[dict]   = []
        self.learned_ids: set      = set()  # condition_ids ya en el historial (dedup)
        self._price_buf: list      = []    # (timestamp_rel, chainlink_price)
        self._load()

    # ── API pública ───────────────────────────────────────────────────────────

    def sync_from_results(self, csv_rows: list[dict]) -> None:
        """
        Reconstruye el historial desde results.csv (registro COMPLETO de apuestas
        resueltas). Esto evita los huecos por reinicio: el aprendizaje en memoria
        (pending_learn) se pierde al reiniciar, pero el CSV lo tiene todo.
        """
        hist, ids = [], set()
        for r in csv_rows:
            if r.get("mode") != "directional":
                continue
            up = r.get("up_filled") == "True"
            dn = r.get("down_filled") == "True"
            if not (up or dn):
                continue
            winner = r.get("winner", "")
            if winner not in ("Up", "Down"):
                continue
            side = "up" if up else "down"
            won  = (side == "up" and winner == "Up") or (side == "down" and winner == "Down")
            hist.append({"side": side, "won": won})
            ids.add(r.get("condition_id", ""))
        self.history = hist
        self.learned_ids = ids
        self._adapt()
        self._save()
        print(f"  [Brain] Sincronizado desde results.csv: {len(hist)} ops")

    def record_price(self, cl_price: float, secs_elapsed: float) -> None:
        """Registra precio Chainlink actual y actualiza volatilidad."""
        self._price_buf.append((secs_elapsed, cl_price))
        if len(self._price_buf) > 200:
            self._price_buf.pop(0)
        self._update_vol()

    def evaluate(self,
                 cl_open:  float, cl_now:   float,
                 spot_open: float, spot_now: float,
                 up_ask:   float, down_ask:  float,
                 secs_elapsed: float, secs_left: float) -> list[Signal]:
        """
        Evalúa si hay señal de entrada en algún lado.
        Retorna lista de Signal (normalmente 0 o 1).
        """
        signals = []

        cl_diff   = cl_now   - cl_open     # positivo = Up ganando según Chainlink
        spot_diff = spot_now - spot_open   # positivo = Up ganando según Binance

        # ── Filtro de coherencia: Chainlink y Binance deben coincidir ──────────
        same_direction = (cl_diff > 0 and spot_diff > 0) or \
                         (cl_diff < 0 and spot_diff < 0)

        # ── EDGE 1: Apertura de ventana ────────────────────────────────────────
        if secs_elapsed <= OPEN_WINDOW_SECS:
            sig = self._eval_open_window(
                cl_diff, spot_diff, up_ask, down_ask,
                secs_elapsed, secs_left, same_direction)
            if sig:
                signals.append(sig)

        # ── EDGE 2: Lag de oráculo (T=120s-10min) ─────────────────────────────
        elif 120 < secs_elapsed and secs_left > 90:
            if same_direction:
                sig = self._eval_oracle_lag(
                    cl_diff, spot_diff, up_ask, down_ask,
                    secs_elapsed, secs_left)
                if sig:
                    signals.append(sig)

        return signals

    def record_outcome(self, winner: str, signals: list[Signal],
                       condition_id: str | None = None) -> None:
        """Registra resultado y adapta. Dedup por condition_id (no recontar)."""
        if condition_id and condition_id in self.learned_ids:
            return   # ya contado (vía sync desde CSV o aprendizaje previo)
        for s in signals:
            won = s.side.lower() == winner.lower()
            self.history.append({"side": s.side, "won": won})
        if condition_id:
            self.learned_ids.add(condition_id)
        self._adapt()
        self._save()

    def reset_window(self) -> None:
        self._price_buf.clear()

    def summary(self) -> str:
        total = len(self.history)
        if total == 0:
            return (f"Brain: sin historial | "
                    f"vol=${self.vol_per_sec:.3f}/s | "
                    f"threshold={self.edge_threshold:.0%}")
        wins = sum(1 for h in self.history if h["won"])
        recent = self.history[-20:]
        rwr = sum(1 for h in recent if h["won"]) / len(recent)
        return (f"Brain: {wins}/{total} ({wins/total:.0%}) | "
                f"ult.20: {rwr:.0%} | "
                f"vol=${self.vol_per_sec:.3f}/s | "
                f"thr={self.edge_threshold:.0%}")

    # ── Evaluadores de edge ───────────────────────────────────────────────────

    def _eval_open_window(self, cl_diff, spot_diff, up_ask, down_ask,
                          secs_elapsed, secs_left, same_direction) -> Signal | None:
        """
        Edge 1: el mercado acaba de abrir en ~50/50 pero el precio ya se movió.
        Condición: movimiento spot > MIN_MOVE_OPEN, pagar < MAX_ENTRY_PRICE.
        """
        abs_spot = abs(spot_diff)
        if abs_spot < MIN_MOVE_OPEN:
            return None

        p_true = self._p_up(cl_diff, secs_left)

        if spot_diff > 0:   # Up ganando
            side, market_price, p_side = "up", up_ask, p_true
        else:               # Down ganando
            side, market_price, p_side = "down", down_ask, 1 - p_true

        if not self._price_ok(market_price):
            return None

        edge = p_side - market_price
        if edge < self.edge_threshold:
            return None

        return Signal(side=side, edge_type="open_window",
                      market_price=market_price, p_true=p_side,
                      edge=edge, btc_diff=cl_diff, spot_diff=spot_diff,
                      secs_elapsed=secs_elapsed, secs_left=secs_left)

    def _eval_oracle_lag(self, cl_diff, spot_diff, up_ask, down_ask,
                         secs_elapsed, secs_left) -> Signal | None:
        """
        Edge 2: Chainlink confirma dirección, spot va más lejos.
        El mercado no ha reflejado aún la última actualización de Chainlink.
        """
        abs_cl = abs(cl_diff)
        if abs_cl < MIN_MOVE_MID:
            return None

        # Spot debe ir MÁS lejos que Chainlink (lag pendiente de confirmación)
        if not (abs(spot_diff) > abs_cl * 0.8):
            return None

        p_true = self._p_up(cl_diff, secs_left)

        if cl_diff > 0:
            side, market_price, p_side = "up", up_ask, p_true
        else:
            side, market_price, p_side = "down", down_ask, 1 - p_true

        if not self._price_ok(market_price):
            return None

        edge = p_side - market_price
        if edge < self.edge_threshold:
            return None

        return Signal(side=side, edge_type="oracle_lag",
                      market_price=market_price, p_true=p_side,
                      edge=edge, btc_diff=cl_diff, spot_diff=spot_diff,
                      secs_elapsed=secs_elapsed, secs_left=secs_left)

    # ── Internos ──────────────────────────────────────────────────────────────

    def _price_ok(self, price: float | None) -> bool:
        """Rango de precio aceptable para entrada."""
        if price is None:
            return False
        return MIN_ENTRY_PRICE <= price <= MAX_ENTRY_PRICE

    def _p_up(self, cl_diff: float, secs_left: float) -> float:
        """P(Up gana) usando random walk sobre Chainlink."""
        if secs_left <= 1:
            return 1.0 if cl_diff > 0 else (0.0 if cl_diff < 0 else 0.5)
        vol = max(self.vol_per_sec, 0.10)
        z = cl_diff / (vol * math.sqrt(secs_left))
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))

    def _update_vol(self) -> None:
        buf = self._price_buf
        if len(buf) < 4:
            return
        diffs = [abs(buf[i][1] - buf[i-1][1]) for i in range(1, len(buf))]
        dt    = [buf[i][0] - buf[i-1][0] for i in range(1, len(buf))]
        # $/segundo promedio
        rates = [d/t for d, t in zip(diffs, dt) if t > 0]
        if rates:
            new_vol = sum(rates) / len(rates)
            self.vol_per_sec = 0.8 * self.vol_per_sec + 0.2 * new_vol

    MIN_OPS_TO_ADAPT = 20   # no tocar el threshold hasta tener masa estadística

    def _adapt(self) -> None:
        """
        Ajusta edge_threshold según rendimiento reciente.
        Solo adapta con >=20 ops reales — adaptar antes es perseguir ruido
        (una racha de 8 trades puede ser solo el régimen de mercado del momento).
        """
        if len(self.history) < self.MIN_OPS_TO_ADAPT:
            return
        recent = self.history[-20:]
        wr = sum(1 for h in recent if h["won"]) / len(recent)
        if wr < 0.45:
            self.edge_threshold = min(0.25, self.edge_threshold + 0.02)
            print(f"  [Brain] WR bajo ({wr:.0%}) → threshold sube a {self.edge_threshold:.0%}")
        elif wr > 0.65:
            self.edge_threshold = max(0.05, self.edge_threshold - 0.01)
            print(f"  [Brain] WR alto ({wr:.0%}) → threshold baja a {self.edge_threshold:.0%}")

    def _load(self) -> None:
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, encoding="utf-8") as f:
                    d = json.load(f)
                self.vol_per_sec    = d.get("vol", self.vol_per_sec)
                self.edge_threshold = d.get("threshold", self.edge_threshold)
                self.history        = d.get("history", [])
                self.learned_ids    = set(d.get("learned_ids", []))
                print(f"  [Brain] Cargado: {len(self.history)} ops | "
                      f"threshold={self.edge_threshold:.0%} | "
                      f"vol=${self.vol_per_sec:.3f}/s")
            except Exception:
                pass

    def _save(self) -> None:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "vol":         round(self.vol_per_sec, 6),
                "threshold":   round(self.edge_threshold, 6),
                "history":     self.history[-300:],
                "learned_ids": list(self.learned_ids)[-300:],
            }, f, indent=2)
