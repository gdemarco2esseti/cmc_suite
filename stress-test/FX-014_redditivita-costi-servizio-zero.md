# FX-014 — Redditività: con ABC collegato il costo di servizio e di sostegno vale 0 e i margini M1..M4 coincidono

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | gap register PRF (margini gerarchici cliente) · VG-4 · SC-044 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_user |
| DL collegato | — |

## Comportamento osservato
1. Esecuzione "Video redditività Q4 1745" (calcolata): ricavi 282.500,00, utile operativo 159.597,16.
2. Fatti di profitto: costo variabile totale −122.902,84, ma "Costo di sostegno del prodotto" 0,00 su tutte e 4 le righe.
3. Margini clienti: M1 = M2 = M3 = M4 per ogni cliente (A 57.846,31; B 43.281,18; C 38.163,33; D 20.306,34).

→ I costi ABC di servizio al cliente e di sostegno non arrivano ai margini. La piramide dei margini è piatta.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-044_screenshot.png`

## Comportamento atteso
Con un'esecuzione ABC collegata e calcolata nel periodo, i costi delle attività attribuite a clienti e prodotti scendono nei livelli di margine. M1 ≥ M2 ≥ M3 ≥ M4, con differenze pari ai costi di sostegno e di servizio assegnati.

## AC di regressione
- **DATO** un'esecuzione ABC calcolata nel periodo, con costo di attività di servizio al cliente > 0 per il cliente A
- **QUANDO** si calcola l'esecuzione di redditività dello stesso periodo
- **ALLORA** per il cliente A il costo di servizio è > 0 e M3 (o il livello previsto) è minore di M2 della stessa cifra

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-066 · PASS
