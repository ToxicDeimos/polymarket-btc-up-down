"""
saldo.py — ¿Cuanto USDC hay en la cuenta? (no opera, no imprime credenciales)

Sirve para dos cosas: consultar el saldo a mano, y COMPROBAR LAS UNIDADES antes de cablear el freno
automatico de stalebot. La API de Polygon devuelve los importes en micro-USDC (6 decimales), pero un
freno de seguridad no se construye sobre una suposicion: si el factor estuviera mal, el bot se
pararia solo el primer dia creyendo que hay 0,0000156 $. Aqui se ve el valor CRUDO y el interpretado,
y se contrasta con lo que dice la web.

    cd ~/polymarket-btc-up-down/research && python3 saldo.py

Usa las mismas credenciales que stalebot (research/.env, cargado por el propio stalebot).
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stalebot                                    # noqa: E402  (carga research/.env al importarse)


def main():
    if not os.environ.get("PRIVATE_KEY") and not os.environ.get("POLY_PK"):
        print("falta PRIVATE_KEY: no hay con que preguntar. Revisa research/.env"); return
    from py_clob_client_v2 import AssetType, BalanceAllowanceParams
    cli = stalebot.arranca_cliente()

    kw = {"asset_type": AssetType.COLLATERAL}
    st = os.environ.get("POLY_SIGNATURE_TYPE")
    if st: kw["signature_type"] = int(st)

    # La conexion de la Pi con el CLOB NO es fiable: "Connection reset by peer" en el handshake
    # TLS, y "read operation timed out" en el propio bot. Una consulta de saldo que se rinde al
    # primer intento no sirve para nada. Se reintenta y, si falla del todo, se dice CUANTAS veces
    # fallo, que es informacion sobre la conexion y no solo sobre el saldo.
    ultimo = None
    for i in range(4):
        try:
            r = cli.get_balance_allowance(BalanceAllowanceParams(**kw))
            if i: print(f"  (funciono al intento {i+1}: la conexion falla a ratos)")
            break
        except Exception as e:
            ultimo = e
            print(f"  intento {i+1}/4 fallido: {type(e).__name__}: {str(e)[:80]}")
            time.sleep(1.5 * (i + 1))
    else:
        print(f"\n⚠ 4 intentos fallidos. La Pi no esta llegando al CLOB ahora mismo.")
        print(f"  ultimo error: {type(ultimo).__name__}: {str(ultimo)[:120]}")
        print("  No es un fallo de este script: el bot registra los mismos timeouts.")
        return
    d = r if isinstance(r, dict) else getattr(r, "__dict__", {"resp": str(r)})

    print("\nrespuesta cruda del CLOB:")
    for k, v in d.items():
        print(f"  {k:<14} {v}")

    raw = d.get("balance")
    if raw is None:
        print("\n⚠ la respuesta no trae 'balance'. Con esos nombres de campo hay que ajustar el codigo.")
        return
    try: n = float(raw)
    except Exception:
        print(f"\n⚠ 'balance' no es un numero: {raw!r}"); return

    print(f"\n  si son micro-USDC (6 decimales):  {n/1e6:,.4f} $   <- lo esperado")
    print(f"  si ya viniera en dolares:         {n:,.4f} $")
    print("\n  Comprueba cual de los dos coincide con el saldo que ves en la web de Polymarket.")
    print("  El freno automatico de stalebot usa el PRIMERO (dividir entre 1e6).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
