# FX-012 — La curva a S mostra una campana: i valori cumulati tornano a 0 dopo la data di stato e manca PV/EV

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | media |
| Tracciabilità | gap register PRJ (EVM, curva a S) · VG-2 · SC-042 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_user |
| DL collegato | — |

## Comportamento osservato
1. Costing di commessa → Curva a S.

→ Il grafico predefinito mostra solo "AC (cumulato)". Il valore è 0 a gennaio e febbraio, ha un picco a marzo (circa 62.000, la somma di 3 elaborazioni) e torna a 0 da aprile a giugno.

Elenco "Video EVM marzo 1812": EV e AC valgono 12.782,50 e 20.670,00 al 1 marzo, poi 0,00 e 0,00 al 1 aprile, maggio e giugno. PV invece cresce (21.722,50 → 28.830,00 → 30.350,00).

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-042_screenshot.png`, `SC-042_trace.zip`

## Comportamento atteso
La curva a S confronta PV, EV e AC **cumulati**. Dopo la data di stato EV e AC restano all'ultimo valore cumulato, oppure non vengono mostrati: non tornano mai a 0. I mesi di più elaborazioni EVM non si sommano fra loro.

## AC di regressione
- **DATO** un'elaborazione EVM con data di stato 31/03 e valori cumulati EV 12.782,50 e AC 20.670,00 a marzo
- **QUANDO** l'utente apre la curva a S di quella elaborazione
- **ALLORA** vede le tre serie PV, EV e AC, ciascuna non decrescente nel tempo, ed EV/AC non scendono a 0 dopo marzo

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-064 · FAIL: il grafico apriva in modalità 'In pila' e Odoo riempie con 0 i mesi senza EV/AC. Decisione dell'utente del 2026-10-11: EV e AC piatte dopo la data di stato, grafico non in pila. Corretto, da rilanciare.
- 2026-10-11_cmc-suite_r2 · SC-078 · PASS
