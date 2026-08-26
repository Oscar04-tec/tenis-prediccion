"""
API de predicción. Levantar con:
    uvicorn api:app --host 0.0.0.0 --port 8000

Endpoints:
    GET  /salud                 estado y frescura de los datos
    GET  /jugadores?q=alcaraz   busca nombres exactos en la base
    POST /predecir              probabilidad del partido
    POST /valor                 probabilidad + EV + stake contra tus cuotas
"""
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from elo import SUP, esperado, calibrar, sin_vig, mezclar_con_mercado, kelly
from ingesta import DB, frescura

app = FastAPI(title="Tenis Predicción", version="1.0")


def rating(jugador: str, superficie: str):
    con = sqlite3.connect(DB)
    q = "SELECT elo FROM ratings WHERE jugador=? AND superficie=?"
    gen = con.execute(q, (jugador, "ALL")).fetchone()
    sup = con.execute(q, (jugador, superficie)).fetchone()
    con.close()
    if not gen:
        raise HTTPException(404, f"Sin rating para «{jugador}». Prueba /jugadores?q=…")
    return gen[0], (sup[0] if sup else gen[0])


class Partido(BaseModel):
    jugador_a: str
    jugador_b: str
    superficie: str = Field("Hard", description="Hard, Clay, Grass o Carpet")
    ajuste_a: float = Field(0, description="Puntos Elo: fatiga −25, lesión −40")
    ajuste_b: float = 0


class Consulta(Partido):
    cuota_a: float
    cuota_b: float
    cuota_ref_a: Optional[float] = None
    cuota_ref_b: Optional[float] = None
    peso_modelo: float = 0.35
    bankroll: float = 5000


@app.get("/salud")
def salud():
    dias = frescura()
    return {
        "datos_con_retraso_dias": dias,
        "confiable_para_hoy": dias <= 3,
        "aviso": None if dias <= 3 else
        "Los datos están rancios. Cualquier predicción de partidos recientes "
        "usa ratings desactualizados. Revisa la ingesta antes de apostar.",
    }


@app.get("/jugadores")
def jugadores(q: str, limite: int = 15):
    con = sqlite3.connect(DB)
    r = con.execute(
        "SELECT DISTINCT jugador FROM ratings WHERE jugador LIKE ? "
        "AND superficie='ALL' ORDER BY jugador LIMIT ?",
        (f"%{q}%", limite),
    ).fetchall()
    con.close()
    return [x[0] for x in r]


def _probabilidad(p: Partido):
    if p.superficie not in SUP:
        raise HTTPException(400, f"Superficie inválida. Usa: {list(SUP)}")
    w = SUP[p.superficie]
    ga, sa = rating(p.jugador_a, p.superficie)
    gb, sb = rating(p.jugador_b, p.superficie)
    ma = w * sa + (1 - w) * ga + p.ajuste_a
    mb = w * sb + (1 - w) * gb + p.ajuste_b
    cruda = esperado(ma, mb)
    return {
        "elo_a": round(ma, 1), "elo_b": round(mb, 1),
        "p_cruda_a": round(float(cruda), 4),
        "p_modelo_a": round(float(calibrar(cruda)), 4),
    }


@app.post("/predecir")
def predecir(p: Partido):
    r = _probabilidad(p)
    r["p_modelo_b"] = round(1 - r["p_modelo_a"], 4)
    r["frescura_dias"] = frescura()
    return r


@app.post("/valor")
def valor(c: Consulta):
    base = _probabilidad(c)
    pa = base["p_modelo_a"]

    if c.cuota_ref_a and c.cuota_ref_b:
        fa, fb, over = sin_vig(c.cuota_ref_a, c.cuota_ref_b)
        pa = mezclar_con_mercado(pa, fa, c.peso_modelo)
        desacuerdo = round(abs(base["p_modelo_a"] - fa), 4)
        mercado = {"p_justa_a": round(fa, 4), "margen_ref": round(over, 4)}
    else:
        desacuerdo, mercado = None, None

    pb = 1 - pa
    lados = []
    for nombre, prob, cuota in [
        (c.jugador_a, pa, c.cuota_a),
        (c.jugador_b, pb, c.cuota_b),
    ]:
        k = kelly(prob, cuota)
        lados.append({
            "jugador": nombre,
            "probabilidad": round(prob, 4),
            "cuota": cuota,
            "ev": round(prob * cuota - 1, 4),
            "stake_sugerido": round(k * c.bankroll, 2),
        })
    mejor = max(lados, key=lambda x: x["ev"])

    avisos = []
    d = frescura()
    if d > 3:
        avisos.append(f"Datos con {d} días de retraso.")
    if mercado is None:
        avisos.append("Sin cuota de referencia: el modelo va sin calibrar contra el mercado.")
    if desacuerdo and desacuerdo > 0.15:
        avisos.append(
            f"Desacuerdo de {desacuerdo:.0%} con el mercado. Casi siempre significa "
            "que la casa sabe algo que tú no (lesión, retiro), no que hallaste valor."
        )
    if mejor["ev"] < 0.03:
        avisos.append("EV por debajo del umbral. Lo correcto es no apostar.")

    return {**base, "mercado": mercado, "lados": lados,
            "recomendacion": mejor, "avisos": avisos}
