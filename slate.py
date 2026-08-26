"""
Genera el slate del día: partidos próximos con probabilidad del modelo,
probabilidad justa del mercado y valor esperado.

    export ODDS_API_KEY=tu_llave
    python slate.py

Presupuesto: el plan gratis son 500 peticiones al mes. Cada torneo activo
cuesta 1 crédito por región. Usamos solo 'eu' porque ahí está Pinnacle, que
es la referencia que importa. Con ~8 torneos activos son ~8 créditos por
corrida; a dos corridas diarias son ~480 al mes. Por eso el cron va 2 veces
al día y el script se detiene solo si quedan menos de 40 créditos.
"""
import os, json, sys, time, difflib, datetime as dt
from pathlib import Path
import requests

API = "https://api.the-odds-api.com/v4"
LLAVE = os.environ.get("ODDS_API_KEY", "").strip()
SALIDA = Path("publico")
RATINGS = SALIDA / "ratings.json"

REGIONES = "eu"          # Pinnacle vive aquí
CREDITOS_MINIMOS = 40    # colchón para no quedarnos sin llamadas a media semana
PESO_MODELO = 0.35
UMBRAL_EV = 0.03

# La API no informa la superficie. Se infiere del nombre del torneo.
SUPERFICIE = {
    "french open": "Clay", "roland": "Clay", "madrid": "Clay", "rome": "Clay",
    "monte": "Clay", "barcelona": "Clay", "hamburg": "Clay", "estoril": "Clay",
    "munich": "Clay", "umag": "Clay", "gstaad": "Clay", "kitzbuhel": "Clay",
    "bastad": "Clay", "buenos aires": "Clay", "rio": "Clay", "santiago": "Clay",
    "wimbledon": "Grass", "queen": "Grass", "halle": "Grass", "stuttgart": "Grass",
    "eastbourne": "Grass", "mallorca": "Grass", "newport": "Grass", "hertogenbosch": "Grass",
}


def superficie_de(titulo: str) -> str:
    t = titulo.lower()
    for clave, sup in SUPERFICIE.items():
        if clave in t:
            return sup
    return "Hard"


def pedir(ruta, **params):
    """Llama a la API y devuelve (json, creditos_restantes)."""
    params["apiKey"] = LLAVE
    r = requests.get(f"{API}{ruta}", params=params, timeout=45)
    restantes = int(r.headers.get("x-requests-remaining", -1))
    if r.status_code == 401:
        sys.exit("Llave inválida. Revisa el secreto ODDS_API_KEY.")
    if r.status_code == 429:
        sys.exit("Se agotaron las peticiones del mes.")
    r.raise_for_status()
    return r.json(), restantes


def sin_vig(a, b):
    ia, ib = 1 / a, 1 / b
    s = ia + ib
    return ia / s, ib / s, s - 1


def calibrar(p, coef):
    p = min(max(p, 1e-6), 1 - 1e-6)
    import math
    return 1 / (1 + math.exp(-math.log(p / (1 - p)) * coef))


def emparejar(nombre, catalogo, cache={}):
    """La API escribe nombres distinto que la base de resultados.
    difflib resuelve la mayoría; lo que no empareja se descarta y se reporta."""
    if nombre in cache:
        return cache[nombre]
    if nombre in catalogo:
        cache[nombre] = nombre
        return nombre
    cand = difflib.get_close_matches(nombre, catalogo, n=1, cutoff=0.86)
    cache[nombre] = cand[0] if cand else None
    return cache[nombre]


def precios(evento):
    """Mejor cuota disponible por lado, y la de Pinnacle como referencia."""
    mejor, pin = {}, {}
    for casa in evento.get("bookmakers", []):
        for mercado in casa.get("markets", []):
            if mercado.get("key") != "h2h":
                continue
            for o in mercado.get("outcomes", []):
                n, p = o.get("name"), o.get("price")
                if not n or not p:
                    continue
                if p > mejor.get(n, 0):
                    mejor[n] = p
                if casa.get("key") == "pinnacle":
                    pin[n] = p
    return mejor, pin


def main():
    if not LLAVE:
        sys.exit("Falta ODDS_API_KEY. Configúrala como secreto del repositorio.")
    if not RATINGS.exists():
        sys.exit("No existe publico/ratings.json. Corre ingesta.py y exportar.py antes.")

    datos = json.loads(RATINGS.read_text())
    ratings, coef = datos["ratings"], datos["calibracion"]
    pesos = datos["pesos_superficie"]
    catalogo = list(ratings)

    deportes, restantes = pedir("/sports/")
    tenis = [d for d in deportes
             if d.get("group") == "Tennis" and d.get("active") and not d.get("has_outrights")]
    print(f"Torneos de tenis activos: {len(tenis)} · créditos restantes: {restantes}")
    for d in tenis:
        print(f"  · {d['title']}")

    if restantes != -1 and restantes < CREDITOS_MINIMOS + len(tenis):
        print(f"⚠ Solo quedan {restantes} créditos. No se consultan cuotas esta corrida.")
        sys.exit(0)

    partidos, sin_rating = [], set()

    for d in tenis:
        try:
            eventos, restantes = pedir(
                f"/sports/{d['key']}/odds/",
                regions=REGIONES, markets="h2h", oddsFormat="decimal")
        except requests.HTTPError as e:
            print(f"  {d['key']}: {e}")
            continue

        sup = superficie_de(d["title"])
        w = pesos.get(sup, 0.5)

        for ev in eventos:
            a, b = ev.get("home_team"), ev.get("away_team")
            if not a or not b:
                continue
            ma, mb = emparejar(a, catalogo), emparejar(b, catalogo)
            if not ma or not mb:
                sin_rating.add(a if not ma else b)
                continue

            mejor, pin = precios(ev)
            if a not in mejor or b not in mejor:
                continue

            ea = w * ratings[ma].get(sup, ratings[ma]["ALL"]) + (1 - w) * ratings[ma]["ALL"]
            eb = w * ratings[mb].get(sup, ratings[mb]["ALL"]) + (1 - w) * ratings[mb]["ALL"]
            p_mod = calibrar(1 / (1 + 10 ** ((eb - ea) / 400)), coef)

            if a in pin and b in pin:
                pf, _, margen = sin_vig(pin[a], pin[b])
                p_final = PESO_MODELO * p_mod + (1 - PESO_MODELO) * pf
                desacuerdo = abs(p_mod - pf)
            else:
                pf, margen, desacuerdo = None, None, None
                p_final = p_mod

            lados = [
                {"jugador": a, "p": p_final, "cuota": mejor[a],
                 "ev": p_final * mejor[a] - 1, "ref": pin.get(a)},
                {"jugador": b, "p": 1 - p_final, "cuota": mejor[b],
                 "ev": (1 - p_final) * mejor[b] - 1, "ref": pin.get(b)},
            ]
            top = max(lados, key=lambda x: x["ev"])

            partidos.append({
                "torneo": d["title"], "superficie": sup,
                "inicio": ev.get("commence_time"),
                "a": a, "b": b, "elo_a": round(ea), "elo_b": round(eb),
                "p_modelo_a": round(p_mod, 4),
                "p_mercado_a": None if pf is None else round(pf, 4),
                "p_final_a": round(p_final, 4),
                "margen_ref": None if margen is None else round(margen, 4),
                "desacuerdo": None if desacuerdo is None else round(desacuerdo, 4),
                "lados": [{**l, "p": round(l["p"], 4), "ev": round(l["ev"], 4)} for l in lados],
                "mejor": top["jugador"],
                "ev": round(top["ev"], 4),
                "sin_referencia": pf is None,
                "verde": top["ev"] >= UMBRAL_EV and (desacuerdo is None or desacuerdo <= 0.15),
            })
        time.sleep(0.3)

    partidos.sort(key=lambda x: -x["ev"])
    verdes = [p for p in partidos if p["verde"]]

    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "slate.json").write_text(json.dumps({
        "generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "creditos_restantes": restantes,
        "torneos": [d["title"] for d in tenis],
        "total_partidos": len(partidos),
        "con_valor": len(verdes),
        "sin_rating": sorted(sin_rating)[:40],
        "partidos": partidos,
    }, ensure_ascii=False, separators=(",", ":")))

    print(f"\nslate.json — {len(partidos)} partidos, {len(verdes)} con valor")
    if sin_rating:
        print(f"Sin rating ({len(sin_rating)}): {', '.join(sorted(sin_rating)[:8])}…")
    print(f"Créditos restantes: {restantes}")
    for p in partidos[:5]:
        print(f"  {p['ev']:+.1%}  {p['mejor']:<24} @{max(l['cuota'] for l in p['lados']):.2f}  {p['torneo']}")


if __name__ == "__main__":
    main()
