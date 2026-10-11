# FX-017 — Etichette in inglese: statusbar "Draft / Computed" e tipi di centro "Final / Common / Auxiliary"

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | ANOMALIA |
| Gravità | bassa |
| Tracciabilità | i18n it_IT della suite · VG traduzioni · SC-051 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · lingua italiano |
| DL collegato | — |

## Comportamento osservato
- Statusbar "Draft / Computed" in: ABB Budget per attività, Elaborazioni scostamenti (budget), Costo di commessa, Esecuzioni di processo, Esecuzioni di redditività, LCC, Esecuzioni KPI, Rapporti di stato EVM. È invece "Bozza / Calcolato" in ABC e Chiusura.
- Righe di chiusura, "Tipo di centro": "Final / Common", "Auxiliary".

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-051_screenshot.png`

## Comportamento atteso
Con l'utente in italiano tutte le etichette di stato e di tipo centro sono in italiano: Bozza / Calcolato, Finale / Comune, Ausiliario.

## AC di regressione
- **DATO** un utente con lingua italiano
- **QUANDO** apre un run calcolato di ciascun modulo della suite e le righe di una chiusura
- **ALLORA** nessuna etichetta di stato o di tipo centro è in inglese

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-069 · PASS
