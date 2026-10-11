# FX-002 — Una registrazione di costo senza centro di costo viene salvata

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | RF-UX-002 ("non si salva senza centro", `../04_CostManagement_BA_UX_v0.1.md:177`) · SC-027 (passaggio 1) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_user |
| DL collegato | — |

## Comportamento osservato
1. Operazioni → Base di costo → Registrazioni di costo → Nuovo.
2. Descrizione "SC027 senza centro", Costo 100. Nessun centro, elemento o comportamento.
3. Salva.

→ Il record viene salvato senza alcun messaggio. Dopo il reload non compare più nella lista (vedi FX-003).

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-027_*`

## Comportamento atteso
> RF-UX-002: non deve essere possibile salvare una registrazione di costo senza centro di costo.

## AC di regressione
- **DATO** il form di una nuova registrazione di costo
- **QUANDO** l'utente salva senza indicare il centro di costo
- **ALLORA** il salvataggio è rifiutato, il campo centro è evidenziato come obbligatorio e nulla viene registrato

## Note per il developer
Vale per l'inserimento manuale dal menu Registrazioni di costo. Le righe che arrivano dai ponti (fatture, timesheet, MRP) seguono le loro regole di classificazione.

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-054 · PASS
