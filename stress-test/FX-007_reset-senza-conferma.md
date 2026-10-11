# FX-007 — Alcuni "Riporta in bozza" cancellano i risultati senza chiedere conferma

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC (da approvare) |
| Gravità | media |
| Tracciabilità | AD-001 (il reset svuota i risultati) · SC-026 (passaggio 1) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_user |
| DL collegato | DL-003 |

## Comportamento osservato
1. Direct costing e decisioni → Decisioni → nuova decisione calcolata (esito "Indifferente").
2. Riporta in bozza.

→ Il reset avviene subito, senza dialog, e la sezione Esito sparisce. Su Costing e Chiusura lo stesso bottone chiede invece "Riportando in bozza i risultati calcolati vengono cancellati. Continuare?".

Dall'analisi delle viste lo stesso comportamento riguarda anche: varianze ABB, riconciliazione COGE-COAN, scenari (3), target costing/LCC (4). Questi casi non sono stati verificati uno per uno a schermo.

## Comportamento atteso (proposto, DL-003)
Ogni reset che cancella risultati chiede la stessa conferma, in tutta la suite.

## AC di regressione
- **DATO** un run calcolato di ciascun tipo (decisione, varianza ABB, riconciliazione COGE, scenari, target/LCC)
- **QUANDO** l'utente clicca Riporta in bozza
- **ALLORA** compare la conferma standard, e annullando i risultati restano intatti

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-059 · PASS
