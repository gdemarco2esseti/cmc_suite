# FX-015 — Scostamenti ABB: la risorsa "Team consulenti" ha consuntivo 0 nonostante i costi registrati

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | gap register ABM/ABB (scostamenti di spesa e utilizzo) · VG-5 · SC-045 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_user |
| DL collegato | — |

## Comportamento osservato
1. ABB → "Video scostamenti Q1" → Delivery Software / Team consulenti.

→ Costo a budget 15.000,00, **costo effettivo 0,00**, assegnato 0,00, inutilizzato 3.841,41, scostamento di utilizzo −11.158,59, scostamento di spesa +15.000,00. Per Piattaforma cloud invece il costo effettivo è 9.744,00.

Secondo l'anomalia della video-guide sul centro ed elemento della risorsa ci sono circa 121.800 di costo in Q1. Dal collaudo il confronto con le registrazioni non è stato possibile: la ricerca per descrizione non trova nulla e le righe viste sono di dicembre. Da confermare in sviluppo sui dati.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-045_screenshot.png`

## Comportamento atteso
Il costo effettivo di una risorsa ABB è la somma dei costi registrati sul suo centro ed elemento nel periodo dello scostamento. Lo scostamento di spesa di +15.000 ("tutto risparmiato") non deve nascere dalla mancata lettura del consuntivo.

## AC di regressione
- **DATO** una risorsa ABB legata a centro C ed elemento E, e costi registrati per 121.800 su C/E nel trimestre
- **QUANDO** si calcola lo scostamento ABB del trimestre
- **ALLORA** il costo effettivo della risorsa è 121.800 e lo scostamento di spesa è budget − 121.800

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-067 · PASS
