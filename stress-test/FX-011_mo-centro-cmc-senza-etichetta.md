# FX-011 — Nell'ordine di produzione il centro CMC compare come casella senza etichetta: il ponte MRP non è usabile da UI

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | gap register INT (ponte MRP) · VG / r1 SC-038 · SC-050 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo admin (Manager controllo di gestione) |
| DL collegato | — |

## Comportamento osservato
1. Produzione → Nuovo ordine di produzione, prodotto con distinta base.
2. Si cerca dove indicare il centro di costo CMC.

→ Subito dopo "Distinta base" c'è una casella **senza etichetta**: nessun testo contiene "centro" o "CMC", e la scheda "Varie" non ha nulla. L'utente non sa che quella casella è il centro CMC. Senza centro, l'MO non porta il costo materiali nella suite: in r1 l'MO WH/MO/00009 ha generato solo 1,67 di MOD e nessuna riga materiali.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-050_screenshot.png`, `SC-050_screenshot_varie.png` · r1: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-038_*`

## Comportamento atteso
Il form dell'MO mostra un campo con un'etichetta chiara ("Centro di costo CMC"), visibile agli utenti del controllo di gestione. Compilato, il costo materiali dell'MO arriva una sola volta sul centro scelto.

## AC di regressione
- **DATO** un utente Controlling User con diritti di produzione
- **QUANDO** crea un ordine di produzione
- **ALLORA** vede il campo "Centro di costo CMC" con la sua etichetta
- **E QUANDO** lo imposta su un centro finale e completa l'MO con componenti dal costo totale 15
- **ALLORA** fra le registrazioni di costo del centro c'è il costo materiali 15, una volta sola

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-063 · PASS
