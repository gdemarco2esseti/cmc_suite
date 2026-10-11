# FX-020 — Costo standard: il roll-up delle schede senza distinta base non fa nulla e non dice nulla

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC |
| Gravità | media |
| Tracciabilità | DL-002 (stesso schema di FX-004: un'azione senza dati deve dire dove inserirli) · SC-062 (nota dell'esecutore) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r3 · ruolo controlling_manager |
| DL collegato | DL-002 |

## Comportamento osservato
Versione di piano Standard → roll-up delle schede su "Table" e "Table Kit", prodotti senza distinta di produzione nell'azienda Delta: non viene creata nessuna scheda e non compare alcun messaggio.

Evidenze: `stress-test/evidenze/2026-10-11_cmc-suite_r1/SC-062_screenshot.png`, `SC-062_trace.zip`

## Comportamento atteso
Se un prodotto non ha una distinta di produzione nell'azienda della versione, il roll-up si ferma con un messaggio che nomina il prodotto e dice dove crearla. Non viene creato nulla.

## Correzione (fix/stress-2026-10, in attesa di rilancio)
`cmc_standard_cost/models/cmc_standard_rollup.py` `action_cmc_std_rollup`: UserError che elenca i prodotti senza distinta "Produci questo prodotto" e indica il percorso Produzione > Prodotti > Distinte base.

## AC di regressione
- **DATO** una versione Standard in bozza e un prodotto senza distinta di produzione
- **QUANDO** il manager lancia il roll-up delle schede
- **ALLORA** compare un messaggio che nomina il prodotto e il percorso per creare la distinta, e nessuna scheda viene creata

## Rilancio
- 2026-10-11_cmc-suite_r2 · SC-077 · PASS
