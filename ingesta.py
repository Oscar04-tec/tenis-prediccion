"""
Ingesta de resultados desde TennisMyLife (fuente viva, licencia MIT).

    python ingesta.py              # reconstruye el histórico completo
    python ingesta.py --wta        # incluye WTA
    python ingesta.py --challenger # incluye Challenger
    python ingesta.py --listar     # muestra qué archivos publica la fuente

Cron sugerido:
    0  5 * * 0  cd /opt/tenis && .venv/bin/python ingesta.py >> ingesta.log 2>&1
    0 */6 * * * cd /opt/tenis && .venv/bin/python ingesta.py >> ingesta.log 2>&1

NOTA: la versión anterior usaba el mirror de GitHub (Tennismylife/TML-Database),
que quedó congelado en enero de 2026. El proyecto migró a su sitio propio y ahí
sí publica resultados en vivo, incluido ongoing_tourneys.csv con los torneos en
curso. No uses el repo de GitHub.
"""
import sqlite3, io, sys, time, datetime as dt
import pandas as pd, requests
from elo import correr, cargar_df

DB = "tenis.db"
BASE = "https://stats.tennismylife.org/data"
INDICE = "https://stats.tennismylife.org/api/data-files"
DESDE = 2015

UA = {"User-Agent": "tenis-prediccion/1.0 (proyecto personal de analisis)"}


def bajar(nombre: str) -> pd.DataFrame | None:
    """Descarga un CSV con reintentos. Nunca martillea el servidor."""
    url = f"{BASE}/{nombre}"
    for intento in range(3):
        try:
            r = requests.get(url, timeout=90, headers=UA)
            if r.status_code == 200:
                d = pd.read_csv(io.StringIO(r.text), low_memory=False)
                print(f"  {nombre}: {len(d):,} partidos")
                return d
            if r.status_code == 404:
                return None
            print(f"  {nombre}: HTTP {r.status_code}, reintento {intento + 1}")
        except requests.RequestException as e:
            print(f"  {nombre}: {e}, reintento {intento + 1}")
        time.sleep(3 * (intento + 1))
    return None


def listar_disponibles():
    """Consulta el índice oficial en vez de adivinar nombres de archivo."""
    try:
        r = requests.get(INDICE, timeout=30, headers=UA)
        if r.status_code == 200:
            return [f.get("name", "") for f in r.json().get("files", [])]
    except Exception as e:
        print(f"Índice no disponible: {e}")
    return []


def recolectar(wta=False, challenger=False):
    año = dt.date.today().year
    archivos = [f"{a}.csv" for a in range(DESDE, año + 1)]
    archivos.append("ongoing_tourneys.csv")   # torneos en curso, en vivo
    if wta:
        archivos += [f"{a}_wta.csv" for a in range(DESDE, año + 1)]
        archivos.append("wta_ongoing_tourneys.csv")
    if challenger:
        archivos += [f"{a}_challenger.csv" for a in range(DESDE, año + 1)]
        archivos.append("challenger_ongoing_tourneys.csv")

    partes = []
    for nombre in archivos:
        d = bajar(nombre)
        if d is not None and len(d):
            d["fuente"] = nombre
            partes.append(d)
        time.sleep(0.4)   # cortesía con un servidor gratuito
    return partes


def deduplicar(d: pd.DataFrame) -> pd.DataFrame:
    """ongoing_tourneys se solapa con el CSV del año; nos quedamos con lo más reciente."""
    llave = [c for c in ["tourney_id", "match_num", "winner_name", "loser_name"]
             if c in d.columns]
    antes = len(d)
    d = d.drop_duplicates(subset=llave, keep="last")
    if antes != len(d):
        print(f"Duplicados eliminados: {antes - len(d):,}")
    return d


def construir(db=DB, wta=False, challenger=False):
    print("Descargando desde TennisMyLife…")
    partes = recolectar(wta, challenger)
    if not partes:
        sys.exit("No se descargó nada. Revisa la conexión o si la fuente cambió.")

    d = cargar_df(deduplicar(pd.concat(partes, ignore_index=True)))
    print(f"\nTotal utilizable: {len(d):,} "
          f"({d.fecha.min().date()} → {d.fecha.max().date()})")

    print("Calculando Elo partido por partido…")
    pred, elo, elo_sup = correr(d)

    con = sqlite3.connect(db)
    d.to_sql("partidos", con, if_exists="replace", index=False)
    pred.to_sql("predicciones", con, if_exists="replace", index=False)
    pd.DataFrame(
        [{"jugador": j, "elo": v, "superficie": "ALL"} for j, v in elo.items()]
        + [{"jugador": j, "elo": v, "superficie": s} for (j, s), v in elo_sup.items()]
    ).to_sql("ratings", con, if_exists="replace", index=False)

    con.execute("CREATE INDEX IF NOT EXISTS ix_rat ON ratings(jugador, superficie)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('ultima_fecha_datos', ?)",
                (str(d.fecha.max().date()),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('actualizado', ?)",
                (dt.datetime.now().isoformat(timespec="seconds"),))
    con.commit()
    con.close()

    atraso = (dt.date.today() - d.fecha.max().date()).days
    print(f"\nListo. {len(elo):,} jugadores con rating. Atraso: {atraso} días.")
    if atraso > 10:
        print("⚠ La fuente dejó de actualizarse. Verifica antes de predecir partidos de hoy.")


def frescura(db=DB) -> int:
    """Días de retraso de los datos. Consúltalo antes de servir cualquier predicción."""
    con = sqlite3.connect(db)
    try:
        f = con.execute(
            "SELECT valor FROM meta WHERE clave='ultima_fecha_datos'").fetchone()
    finally:
        con.close()
    return 9999 if not f else (dt.date.today() - dt.date.fromisoformat(f[0])).days


if __name__ == "__main__":
    if "--listar" in sys.argv:
        archivos = listar_disponibles()
        print("\n".join(archivos) if archivos else "El índice no respondió.")
    else:
        construir(wta="--wta" in sys.argv, challenger="--challenger" in sys.argv)
