# FX-013 — Costo di commessa: "le ore … non hanno un centro di costo" non dice cosa fare e cita una commessa diversa

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | media |
| Tracciabilità | RF-UX-010 (messaggi azionabili) · VG-3 · SC-043 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_user |
| DL collegato | — |

## Comportamento osservato
1. Admin registra 2:00 su "Internal" (10 ott).
2. User: Costing di commessa → nuova elaborazione, periodo 10 gen – 31 ott → Calcola.

→ "Operazione non valida: Le ore della commessa Commessa CRM - Cliente A non hanno un centro di costo." Il messaggio non indica come collegare un centro e nomina una commessa diversa da quella appena usata.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-043_*`

## Comportamento atteso
> RF-UX-010: i messaggi dicono cosa fare.

Il messaggio nomina la commessa e le ore davvero interessate e indica l'azione: su quale conto o piano analitico indicare il centro di costo. In alternativa, le ore senza centro vengono riportate in un elenco controllabile invece di bloccare l'intera elaborazione. La scelta va fatta in sviluppo, con la stessa scelta per tutti i run.

## AC di regressione
- **DATO** ore registrate su una commessa il cui conto analitico non ha un centro di costo
- **QUANDO** l'utente calcola il costo di commessa del periodo
- **ALLORA** il messaggio nomina quella commessa, il numero di ore e dove impostare il centro di costo

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-065 · PASS
