# FX-008 — Il Controlling Responsible riceve un errore di accesso entrando nell'app

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | AD-115 (il responsabile consulta i propri centri) · RF-UX-010 · SC-037 (passaggio 2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_responsible |
| DL collegato | — |

## Comportamento osservato
1. Login come Controlling Responsible.
2. Primo accesso al cruscotto dell'app Controllo di gestione.

→ Compare il dialog "Errore di accesso — Accesso ai record "Servizio inter-centro (fornitore -> ricevente)" (cmc.center.service) non consentito. L'operazione è consentita per i seguenti gruppi: Manager controllo di gestione / Utente controllo di gestione…".

Altre osservazioni sullo stesso ruolo:
- in Centri di responsabilità il menu Azioni offre "Archivia", ma il ruolo è in sola lettura;
- "I miei KPI" è vuoto anche con un centro assegnato.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-037_cruscotto_error.png` · `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-037_*`

## Comportamento atteso
> AD-115: il Responsible è un ruolo di sola lettura che vede i centri di cui è responsabile. Il suo ingresso nell'app non deve produrre errori di accesso (RF-UX-010).

## AC di regressione
- **DATO** un utente con il solo gruppo Controlling Responsible
- **QUANDO** apre l'app Controllo di gestione e naviga tra le voci a lui visibili
- **ALLORA** non compare alcun errore di accesso; la prima schermata è una vista che il ruolo può leggere; nessuna azione di modifica (Archivia compreso) è proposta

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-060 · PASS
