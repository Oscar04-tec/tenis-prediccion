# Tenis Predicción — backend

Motor Elo con mezcla por superficie, calibrado y validado sobre 19,024 partidos ATP reales.

---

## Resultados medidos (no estimados)

Backtest temporal: calibración ajustada en 2022–2023, evaluada en 2024–2026. Nunca k-fold aleatorio — en series temporales da resultados falsamente optimistas.

| Método | Acierto | Brier | Log loss |
|---|---|---|---|
| Ranking ATP (pica al mejor rankeado) | 63.0% | — | — |
| Elo general | 63.2% | 0.2232 | — |
| Elo mezclado por superficie | 63.4% | 0.2211 | 0.6311 |
| **Elo mezclado + calibrado** | 63.1% | **0.2193** | **0.6267** |

**Léelo bien: el modelo le gana al ranking ATP por 0.4 puntos porcentuales.** Eso es todo. No es un bug, es lo que da el Elo público. Cualquiera que te venda 70%+ de acierto en tenis ATP está mintiendo o midiendo mal.

### El hallazgo que más importa

El Elo crudo es **sobreconfiado**:

| Dice | Realidad |
|---|---|
| 75% | 67.6% |
| 65% | 60.4% |
| 35% | 40.8% |

Sistemáticamente infla al favorito. Sin corregir esto, el modelo te empuja a apostar favoritos con "valor" que no existe — la forma más rápida de quemar bankroll creyendo que tienes edge.

La corrección es encoger los log-odds por **0.769** (`elo.calibrar`). Tras aplicarla el error por tramo baja a 1–3 puntos.

---

## Los datos

**Resultados: gratis y en vivo.** Fuente: [TennisMyLife](https://stats.tennismylife.org/tennis-match-database), licencia MIT.

| Archivo | Qué trae |
|---|---|
| `data/2026.csv` | año en curso |
| `data/ongoing_tourneys.csv` | torneos en progreso, actualizado en vivo |
| `data/2026_wta.csv` | WTA (más nuevo, menos verificado) |
| `data/2026_challenger.csv` | Challenger |
| `api/data-files` | índice JSON de todo lo publicado |

Ojo con dos trampas:

1. **El repo `tennis_atp` de Jeff Sackmann ya no está público.** Era la fuente estándar durante años. Ya no existe.
2. **El mirror de GitHub `Tennismylife/TML-Database` está congelado desde el 17-ene-2026** aunque su README siga diciendo "live updated". El proyecto migró al sitio web. Si copias un tutorial viejo vas a terminar con datos de siete meses de antigüedad sin darte cuenta.

Por eso `/salud` y cada respuesta de `/valor` devuelven la frescura en días. Que la API responda no significa que la respuesta valga.

**Cuotas: eso sí cuesta.** Los resultados son gratis; los precios de mercado no.

| Fuente | Qué da | Costo |
|---|---|---|
| The Odds API | Cuotas de varias casas, h2h y totales | plan gratis limitado, luego de pago |
| Scraping de casas | Cuotas propias | viola términos, te banean la cuenta, anti-bot agresivo |

Scrapear resultados públicos es una cosa. Scrapear a tu propia casa de apuestas es otra: está en sus términos, detectan el patrón, y el resultado normal es cuenta cerrada con saldo retenido. No vale la pena. Usa The Odds API o captura las cuotas a mano.

---

## Instalación

```bash
git clone <tu-repo> /opt/tenis && cd /opt/tenis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python ingesta.py          # construye tenis.db (tarda unos minutos)
uvicorn api:app --host 0.0.0.0 --port 8000
```

Cron nocturno:

```
0 4 * * * cd /opt/tenis && .venv/bin/python ingesta.py >> ingesta.log 2>&1
```

En tu VPS ponlo detrás de nginx con TLS. No expongas el 8000 directo.

---

## Uso

```bash
curl -s localhost:8000/salud

curl -s -X POST localhost:8000/valor -H 'Content-Type: application/json' -d '{
  "jugador_a": "Carlos Alcaraz",
  "jugador_b": "Alexander Zverev",
  "superficie": "Clay",
  "cuota_a": 1.45, "cuota_b": 2.75,
  "cuota_ref_a": 1.38, "cuota_ref_b": 3.05,
  "bankroll": 5000
}'
```

Respuesta real de esa consulta:

```
p_cruda_a    0.8081   ← Elo sin calibrar (inflado)
p_modelo_a   0.7513   ← tras calibrar
p_justa_a    0.6885   ← mercado sin vig (margen de la casa: 5.25%)
mezcla       0.7105   ← 35% modelo / 65% mercado
EV Alcaraz   +3.0%    stake sugerido $83.85
```

Nota lo que pasó: el modelo decía 80.8%, la calibración lo bajó a 75.1%, y el mercado lo bajó a 71.1%. Cada capa te quitó valor aparente. Eso es correcto — el valor aparente casi siempre es error de medición.

## Cómo funciona

1. **Elo por partido** con K decreciente (`250/(n+5)^0.4`) y peso por nivel de torneo. Grand Slam pesa 1.15×.
2. **Dos ratings paralelos** por jugador: general y por superficie. Se mezclan con peso 0.62 en arcilla y pasto (la especialización pesa más), 0.50 en dura.
3. **Calibración** por encogimiento de log-odds.
4. **Devig** de la cuota de referencia (Pinnacle o Betfair) para sacar la probabilidad justa del mercado.
5. **Mezcla 35/65** hacia el mercado. Tu modelo aporta la desviación; el mercado aporta la calibración.
6. **Kelly fraccionado** al 25%, topado al 5% del bankroll.

## Ajustes manuales

El campo `ajuste_a` / `ajuste_b` mueve el Elo en puntos. El modelo no sabe de lesiones ni de vuelos:

| Situación | Ajuste |
|---|---|
| Partido de 4h en las últimas 48h | −25 |
| Cambio de superficie sin semana de adaptación | −20 |
| Lesión reportada | −40 |
| Vuelta tras 3+ meses fuera | −50 |

Anota siempre por qué lo aplicaste. Si no lo registras, en dos meses no sabrás si tus ajustes ayudaban o estorbaban.

---

## Antes de usarlo con dinero

Este backend **mide** si tienes ventaja; no la crea. Los números de arriba dicen que un Elo público apenas empata con el ranking ATP, y el mercado de Pinnacle ya incorpora todo eso más lesiones, condiciones y flujo de dinero profesional.

El criterio de decisión es el CLV, no el ROI. Corre esto en papel 200 apuestas registrando cuota tomada contra cuota de cierre. Si el CLV promedio sale negativo, el experimento salió: el modelo no le gana al mercado, y lo correcto es parar — no subirle el peso al modelo.

---

## Despliegue gratis (sin servidor)

La API de `api.py` es opcional. Los ratings solo cambian cuando hay partidos nuevos, así que no necesitas un proceso vivo: necesitas un cálculo diario que deje un archivo. Eso es GitHub Actions.

```
GitHub Actions  →  ingesta.py + exportar.py  →  publico/ratings.json  →  Netlify (dashboard)
   (cron gratis)        (25 min/día)              (~150 KB)              (estático, gratis)
```

**Pasos**

1. Sube el repo a GitHub (público, para minutos de Actions ilimitados).
2. Settings → Actions → General → Workflow permissions → **Read and write**.
3. Actions → "Actualizar ratings" → **Run workflow** para probarlo a mano.
4. En el dashboard, lee el JSON:

```js
const r = await fetch(
  "https://raw.githubusercontent.com/USUARIO/REPO/main/publico/ratings.json"
).then(r => r.json());

if (r.atraso_dias > 3) mostrarAviso(`Datos con ${r.atraso_dias} días de retraso`);
```

5. El dashboard React va a Netlify como sitio estático, igual que tu portafolio.

**Costo total: 0.** Sin cold starts, sin tarjeta, sin instancia que se duerma.

### Trampas conocidas

- GitHub **desactiva los workflows programados tras 60 días sin actividad en el repo**, y los commits del propio bot no siempre cuentan. Si un mes ves los ratings congelados, entra a Actions y reactívalo. Ponte un recordatorio bimestral.
- El horario del `cron` es UTC. `0 9 * * *` son las 3:00 AM en Tampico.
- La ejecución programada puede retrasarse en horas pico de GitHub. No es para datos en vivo minuto a minuto.

### Si de verdad quieres la API viva

Las capas gratis con disco persistente prácticamente desaparecieron en 2026 (Fly.io y Heroku ya no son gratis; Koyeb no deja montar volúmenes y duerme a la hora). Dos salidas:

- **Render free**: despliega `api.py` y reconstruye la base al arrancar. Funciona porque `ingesta.py` tarda pocos minutos, pero cada cold start paga ese costo.
- **Tu VPS de DigitalOcean** (~110 MXN/mes): el mismo que ya contemplas para el portal de consultoría. Ahí `cron` + `uvicorn` + nginx funcionan sin trucos.

Para este proyecto, empieza con Actions. Si algún día necesitas cuotas en vivo, ya tendrás que pagar algo de todos modos.
