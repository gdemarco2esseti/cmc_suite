# FX-005 — Lista CVR: la colonna "Esiste il pareggio" mostra codice HTML invece del badge

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | ANOMALIA |
| Gravità | media (visibile a tutti i ruoli, su un indicatore chiave) |
| Tracciabilità | TS-CVR-001 · SC-005 (passaggio 1) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · tutti i ruoli |
| DL collegato | — |

## Comportamento osservato
1. Direct costing e decisioni → CVR / punto di pareggio, vista lista.

→ Nel badge verde della colonna "Esiste il pareggio" compare testo grezzo: `<div class="o-checkbox d-inline-block me-2"> <input id="boolean_checkbox" ...`. I valori calcolati (Q* 15.000, ricavo di pareggio 150.000) sono corretti.

## Comportamento atteso
La colonna mostra un'indicazione leggibile (Sì/No oppure badge verde/rosso), senza codice.

## AC di regressione
- **DATO** almeno un'analisi CVR con pareggio e una senza (margine ≤ 0)
- **QUANDO** l'utente apre la lista CVR
- **ALLORA** la colonna "Esiste il pareggio" mostra per ciascuna un'indicazione leggibile e distinguibile, in italiano e in inglese

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-057 · PASS
