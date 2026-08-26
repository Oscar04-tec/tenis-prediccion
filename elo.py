"""Motor Elo para tenis con mezcla por superficie. Backtest temporal honesto."""
import pandas as pd, numpy as np, glob
from collections import defaultdict

SUP = {"Hard": 0.50, "Clay": 0.62, "Grass": 0.62, "Carpet": 0.55}

def cargar_df(d):
    """Normaliza un dataframe crudo del esquema Sackmann/TML."""
    d = d.dropna(subset=["winner_name", "loser_name", "surface", "tourney_date"])
    d["fecha"] = pd.to_datetime(d.tourney_date, format="%Y%m%d", errors="coerce")
    d = d.dropna(subset=["fecha"]).sort_values(["fecha", "match_num"]).reset_index(drop=True)
    return d


def cargar(patron="m*.csv"):
    return cargar_df(pd.concat(
        [pd.read_csv(f, low_memory=False) for f in sorted(glob.glob(patron))],
        ignore_index=True))


# Encogimiento de log-odds ajustado sobre 2022-2023 y validado en 2024-2026.
# Elo crudo es sobreconfiado: cuando dice 75% la realidad es ~68%.
CALIBRACION = 0.769


def calibrar(p, coef=CALIBRACION):
    """Corrige la sobreconfianza del Elo. Sin esto el modelo apuesta de más a favoritos."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    lo = np.log(p / (1 - p)) * coef
    return 1 / (1 + np.exp(-lo))


def sin_vig(cuota_a, cuota_b):
    """Probabilidad justa del mercado, quitado el margen de la casa."""
    ia, ib = 1 / cuota_a, 1 / cuota_b
    s = ia + ib
    return ia / s, ib / s, s - 1


def mezclar_con_mercado(p_modelo, p_mercado, peso_modelo=0.35):
    """El mercado calibra, tu modelo aporta la desviación. No subas el peso
    hasta que tengas CLV positivo demostrado sobre 200+ apuestas."""
    return peso_modelo * p_modelo + (1 - peso_modelo) * p_mercado


def kelly(p, cuota, fraccion=0.25, tope=0.05):
    b = cuota - 1
    if b <= 0:
        return 0.0
    k = (b * p - (1 - p)) / b
    return float(min(max(0.0, k) * fraccion, tope))

def k_factor(n_partidos, nivel):
    """K decreciente: jugadores nuevos se mueven rápido, establecidos despacio."""
    base = 250 / ((n_partidos + 5) ** 0.4)
    peso_nivel = {"G": 1.15, "M": 1.05, "F": 1.10, "A": 1.0, "D": 0.85, "C": 0.9}
    return base * peso_nivel.get(nivel, 1.0)

def esperado(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))

def correr(d, arranque=1500):
    """Recorre los partidos en orden y actualiza Elo general y por superficie.
    Devuelve el dataframe con la predicción PREVIA a cada partido (sin fuga)."""
    elo = defaultdict(lambda: arranque)
    elo_sup = defaultdict(lambda: arranque)
    n = defaultdict(int)
    n_sup = defaultdict(int)
    filas = []

    for r in d.itertuples():
        w, l, s = r.winner_name, r.loser_name, r.surface
        peso = SUP.get(s, 0.5)
        kw, kl = (w, s), (l, s)

        # Elo mezclado ANTES del partido
        mw = peso * elo_sup[kw] + (1 - peso) * elo[w]
        ml = peso * elo_sup[kl] + (1 - peso) * elo[l]
        p_gen = esperado(elo[w], elo[l])
        p_mix = esperado(mw, ml)

        filas.append({
            "fecha": r.fecha, "surface": s, "nivel": r.tourney_level,
            "n_w": n[w], "n_l": n[l],
            "elo_w": elo[w], "elo_l": elo[l],
            "mix_w": mw, "mix_l": ml,
            "p_gen": p_gen, "p_mix": p_mix,
            "rank_w": r.winner_rank, "rank_l": r.loser_rank,
        })

        # Actualización
        kw_f = k_factor(n[w], r.tourney_level)
        kl_f = k_factor(n[l], r.tourney_level)
        elo[w] += kw_f * (1 - p_gen)
        elo[l] -= kl_f * (1 - p_gen)

        ps = esperado(elo_sup[kw], elo_sup[kl])
        elo_sup[kw] += k_factor(n_sup[kw], r.tourney_level) * (1 - ps)
        elo_sup[kl] -= k_factor(n_sup[kl], r.tourney_level) * (1 - ps)

        n[w] += 1; n[l] += 1; n_sup[kw] += 1; n_sup[kl] += 1

    return pd.DataFrame(filas), elo, elo_sup

def brier(p):
    """El ganador siempre es 'w', así que el resultado real es 1."""
    return np.mean((p - 1) ** 2)

