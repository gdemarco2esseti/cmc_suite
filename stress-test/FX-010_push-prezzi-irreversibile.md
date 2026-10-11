# FX-010 — "Trasferisci prezzi standard" cambia il costo dei prodotti in modo irreversibile, senza dirlo

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC (da approvare) |
| Gravità | alta (valore di magazzino) |
| Tracciabilità | gap register STD (costo standard) · SC-035 (passaggio 2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_manager (+ admin per l'inventario) |
| DL collegato | DL-005 |

## Comportamento osservato
1. Versione standard "Standard SC035": tariffa manodopera Montaggio Z, Roll-up schede (Tavolo 153, Piano del tavolo 90, Pannello in legno 40) → Approva.
2. Trasferisci prezzi standard → Conferma.

→ Il costo in SF cambia per **3 prodotti**: Tavolo 290→153, Piano del tavolo 240→90, Pannello in legno 80→40. La conferma non dice quali né quanti prodotti verranno toccati. Chicago resta invariata, come corretto.

3. Riporta in bozza: la conferma dice soltanto "Il ripristino riapre la versione per la modifica. Continuare?".

→ La versione torna in Bozza ma i costi restano 153/90/40. Non tornano ai valori precedenti e nessun messaggio avvisa che il trasferimento non viene annullato.

Altre osservazioni sulla preparazione:
- "Precompila tariffe" non genera nulla e non mostra messaggi (vedi FX-004);
- i messaggi di blocco del roll-up sono chiari ("centro di lavoro senza centro CMC", "nessuna tariffa di manodopera").

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-035_*` (anche `_blocker1.png`, `_blocker2.png`)

## Comportamento atteso (proposto, DL-005)
- Prima del trasferimento la conferma elenca i prodotti e i costi vecchio → nuovo.
- Dopo il trasferimento il reset della versione è bloccato, oppure ripristina i costi precedenti. Si decide con DL-005.
- In ogni caso l'utente non deve poter credere di aver annullato qualcosa che resta in vigore.

## AC di regressione
- **DATO** una versione standard approvata con schede per N prodotti
- **QUANDO** il manager avvia il trasferimento dei prezzi
- **ALLORA** la conferma mostra gli N prodotti con costo attuale e nuovo
- **E QUANDO** dopo il trasferimento riporta la versione in bozza
- **ALLORA** il sistema blocca l'azione spiegando che i costi sono già stati trasferiti, oppure ripristina i costi precedenti e lo dice (secondo DL-005)

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-062 · BLOCKED: il DB demo non ha schede standard né distinte di produzione per Delta. Coperto dai test automatici (anteprima, blocco del reset, snapshot in sola lettura). Ne è nato FX-020.
- 2026-10-11_cmc-suite_r2 · SC-076 · PASS (anteprima, push, reset bloccato con messaggio, prezzi trasferiti in sola lettura)
