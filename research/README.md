# research/ — Ingeniería inversa del edge en BTC Up/Down 15m

Investigación para encontrar un edge copiable, reverse-engineering las wallets ganadoras
vía la Data API pública de Polymarket. Los `.csv` son datos generados (gitignored).

## Conclusión (jul 2026)

**El edge de los ganadores es EJECUCIÓN (maker), no una señal.** Reverse-engineered dos
ganadores de señales OPUESTAS:
- **izzyaussie** (fade del underdog) y **zmbabwe** (follow momentum): ambas señales las
  **precia bien el mercado** (calibrado, sin edge direccional). Un modelo logístico con 18
  features tampoco bate el precio out-of-sample.
- Pero **ambos son MAKERS**: entran 6-10¢ más baratos que el mercado (izzyaussie +10.3¢,
  zmbabwe +6.2¢). Ahí está su beneficio.
- Al intentar capturarlo mecánicamente: **selección adversa** (nos llenan en los perdedores);
  el maker dinámico murió en datos frescos. La fill-selection no deja huella en trades históricos.

Disciplina aplicada: pre-registro + test en datos frescos. ~7 reconstrucciones murieron OOS.

## El tool ACTIVO

- **`maker_paper.py`** — bot MAKER en papel (DRY), autónomo (stdlib), corre en vivo y mide
  NUESTROS fills reales + selección adversa. Es el único test que un backtest no puede hacer.
  Correr 24/7 (systemd `maker-paper.service`), ~50-100 fills, analizar con **`analyze_paper.py`**.

## Índice de scripts

| Fase | Scripts |
|------|---------|
| Favorito-longshot | `collect_large.py`, `analyze_favlongshot.py`, `measure_spreads.py`, `verdict.py` |
| Lag / velocidad | `latency_test.py` |
| Detección de ganadores | `bots_collect.py`, `analyze_bots.py`, `wallet_expand.py` |
| Reverse-eng. señal | `selector.py`, `selector2.py`, `feature_select.py`, `order_flow.py`, `zmbabwe.py`, `momentum_bt.py` |
| Ejecución maker | `maker_check.py`, `maker_sim.py`, `maker_sim2.py`, `zmbabwe_maker.py` |
| Backtests + OOS | `backtest_*.py`, `fresh_*.py`, `collect_rich.py`, `model_selector.py`, `validate_model.py` |
| **Test en vivo** | **`maker_paper.py`**, `analyze_paper.py` |
