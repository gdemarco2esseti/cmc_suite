# FX-004 — "Precompila righe" cancella in silenzio le righe inserite a mano e non dice perché non trova nulla

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC (da approvare) |
| Gravità | alta (perdita di dati verificata a schermo in SC-031) |
| Tracciabilità | RF-UX-010 (messaggi azionabili) · TS-005 · SC-004, SC-021 (passaggio 1) · SC-031 (passaggio 2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_user |
| DL collegato | DL-002 |

## Comportamento osservato
1. Costo pieno → Elaborazioni di costing → Nuovo, closing "Chiusura Febbraio", periodo febbraio.
2. Precompila righe.

→ Non viene creata nessuna riga e non compare nessun messaggio.
3. Calcola → "Errore di validazione: L'elaborazione di costing Costo pieno Febbraio ha 100.0 di costo di periodo o figurativo ma nessun consumo di driver su cui ripartirlo."

L'utente non capisce che il precompila legge le "Quantità dei driver" registrate per il periodo, e che queste mancano. Lo stesso accade con la copia del costing di gennaio (SC-021).

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-004_*`

**Passaggio 2 (SC-031), verificato a schermo.** Costing "SC031 costing" con 2 righe inserite a mano (Montaggio Z/Vite/Ore servizio/10, Montaggio Y/Vite/Ore servizio/5) → Salva → Precompila righe. Nessuna conferma né messaggio. Dopo la ricarica la tabella "Consumo del driver" è vuota: le righe manuali sono state cancellate.

Stesso schema altrove (SC-035): "Precompila tariffe" della versione standard non genera righe e non mostra alcun messaggio.

Evidenze aggiuntive: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-031_*`

## Comportamento atteso (proposto, DL-002)
Se non trova quantità di driver nel periodo, il precompila lo dice e indica dove inserirle (Base di costo → Quantità dei driver). Non deve mai togliere in silenzio le righe già presenti.

## AC di regressione
- **DATO** un costing in bozza senza quantità di driver registrate nel periodo
- **QUANDO** l'utente preme Precompila righe
- **ALLORA** compare un messaggio che spiega che non ci sono quantità di driver per il periodo e dove inserirle, e le righe eventualmente già presenti restano invariate

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-056 · PASS
