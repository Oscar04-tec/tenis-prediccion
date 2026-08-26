"""
Exporta la base a JSON estático. Esto es lo que consume el dashboard.
Sin servidor: el frontend hace fetch a estos archivos y ya.

    python exportar.py
"""
import sqlite3, json, datetime as dt
from pathlib import Path
from ingesta import DB, frescura

SALIDA = Path("publico")
TOP = 400  # jugadores exportados; el resto casi nunca aparece en Draftea


def exportar():
    SALIDA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)

    # Los TOP jugadores por Elo general
    top = con.execute(
        "SELECT jugador, elo FROM ratings WHERE superficie='ALL' "
        "ORDER BY elo DESC LIMIT ?", (TOP,)
    ).fetchall()
    nombres = {j for j, _ in top}

    ratings = {j: {"ALL": round(e, 1)} for j, e in top}
    for j, s, e in con.execute(
        "SELECT jugador, superficie, elo FROM ratings WHERE superficie!='ALL'"
    ):
        if j in nombres:
            ratings[j][s] = round(e, 1)

    ultima = con.execute(
        "SELECT valor FROM meta WHERE clave='ultima_fecha_datos'"
    ).fetchone()
    n_partidos = con.execute("SELECT COUNT(*) FROM partidos").fetchone()[0]
    con.close()

    payload = {
        "generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ultima_fecha_datos": ultima[0] if ultima else None,
        "atraso_dias": frescura(),
        "partidos_en_base": n_partidos,
        "jugadores": len(ratings),
        "calibracion": 0.769,
        "pesos_superficie": {"Hard": 0.50, "Clay": 0.62, "Grass": 0.62, "Carpet": 0.55},
        "ratings": ratings,
    }

    (SALIDA / "ratings.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    kb = (SALIDA / "ratings.json").stat().st_size / 1024
    print(f"publico/ratings.json — {len(ratings)} jugadores, {kb:.0f} KB, "
          f"atraso {payload['atraso_dias']} días")


if __name__ == "__main__":
    exportar()
