#!/usr/bin/env bash
# =============================================================================
#  respaldo_pi.sh — copia los datos de la Pi al PC, en instantáneas con fecha
# =============================================================================
#
#  El 1 de octubre de 2026 murió la tarjeta SD de la Pi con TODOS los datos de
#  todos los bots dentro y sin ninguna copia. Se recuperó el 98,4% rascando una
#  imagen del disco con un carvador de filas, pero fue un día entero de trabajo
#  y pudo salir mucho peor. Esto existe para que no vuelva a pasar.
#
#  POR QUÉ INSTANTÁNEAS Y NO UN ESPEJO (rsync/robocopy):
#  un espejo copia también la corrupción encima del fichero bueno. Si la Pi
#  escribe basura a las 3 de la mañana, un espejo la propaga y se acabó. Cada
#  ejecución deja aquí un .tgz independiente con su fecha, así que siempre se
#  puede volver a un día anterior. Los CSV comprimen ~6x, así que una copia
#  diaria durante un año cabe de sobra en lo que hay libre.
#
#  QUÉ NO COPIA, y a propósito:
#   · queue_events_*.csv  → 1 GB de grabaciones al milisegundo, estáticas y ya
#                           retiradas (queuewatch no se vuelve a encender)
#   · .env                → son credenciales. Copiarlas automáticamente a otra
#                           máquina es una decisión de seguridad, no de respaldo.
#                           Si las quieres dentro, añádelas a mano y sabiendo.
#
#      bash respaldo_pi.sh
# =============================================================================
set -u
PI="${PI_HOST:-alex@192.168.1.137}"
DEST="${PI_BACKUP_DIR:-/d/respaldo_pi}"
DIARIO="$DEST/respaldo.log"

mkdir -p "$DEST"
F="$DEST/pi_research_$(date +%Y%m%d_%H%M).tgz"
apunta() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$DIARIO"; }

apunta "empezando · destino $F"

# Un solo viaje: la Pi empaqueta y comprime, y el tubo lo escribe aquí.
ssh -o BatchMode=yes -o ConnectTimeout=20 "$PI" \
    "cd ~/polymarket-btc-up-down/research 2>/dev/null && \
     tar czf - --exclude='queue_events*' --exclude='*.env' *.csv *.out 2>/dev/null" > "$F"
rc=$?

if [ $rc -ne 0 ]; then
    apunta "FALLO: ssh/tar devolvió $rc (¿Pi apagada, o la clave pide contraseña?)"
    [ -s "$F" ] || rm -f "$F"          # solo se borra si quedó VACÍO
    exit 1
fi

# Un .tgz que no se puede listar no es una copia, es un fichero. Se comprueba.
if ! tar tzf "$F" >/dev/null 2>&1; then
    apunta "FALLO: el paquete no se puede leer, lo aparto como .CORRUPTO"
    mv "$F" "$F.CORRUPTO"
    exit 1
fi

n=$(tar tzf "$F" | wc -l)
kb=$(( $(stat -c%s "$F" 2>/dev/null || stat -f%z "$F") / 1024 ))
apunta "OK · $n ficheros · ${kb} KB"

# No se borra nada viejo a propósito: el espacio es barato y un borrado
# automático es justo lo que no quieres el día que lo necesitas.
total=$(ls -1 "$DEST"/pi_research_*.tgz 2>/dev/null | wc -l)
apunta "copias guardadas: $total"
