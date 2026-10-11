# FX-009 — Budget vs consuntivo: con due versioni approvate il consuntivo è contato due volte

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC (da approvare) |
| Gravità | alta |
| Tracciabilità | gap register BUD (budget e scostamenti) · SC-032 (passaggio 2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_user |
| DL collegato | DL-004 |

## Comportamento osservato
1. Esistono due versioni di budget approvate per il 2026: "Budget 2026" e "Budget SC032".
2. Registrazione di costo 1.000 su Montaggio Z, 10/03/2026.
3. Budget e scostamenti → Budget vs consuntivo, pivot senza filtro di versione.

→ Il consuntivo di marzo per Montaggio Z vale **−2.000**: lo stesso 1.000 compare sotto ciascuna versione. Allo stesso modo gennaio Montaggio Z vale −11.500, cioè il doppio di 5.750. Il totale dello scostamento (+3.000) è quindi falso.

Nella lista budget e consuntivo stanno su due righe distinte: piano −5.000 / consuntivo 0 / scostamento +5.000 in rosso, e piano 0 / consuntivo −1.000 / scostamento −1.000.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-032_*`

## Comportamento atteso (proposto, DL-004)
Il consuntivo è un fatto unico e non si moltiplica per il numero di versioni. In alternativa, il report impone di scegliere una versione e non mostra mai totali sommati su più versioni.

## AC di regressione
- **DATO** due versioni di budget approvate sullo stesso anno e un costo consuntivo di 1.000 su un centro nel mese
- **QUANDO** l'utente apre Budget vs consuntivo con le impostazioni predefinite
- **ALLORA** il consuntivo del centro nel mese è 1.000 (una volta sola) e lo scostamento è calcolato rispetto a una sola versione, indicata a schermo

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-061 · FAIL: senza filtro il consuntivo si ripete per ogni versione approvata (totale raddoppiato). Decisione dell'utente del 2026-10-11: il report apre raggruppato per versione, con un help che lo spiega. Corretto, da rilanciare.
- 2026-10-11_cmc-suite_r2 · SC-075 · PASS
