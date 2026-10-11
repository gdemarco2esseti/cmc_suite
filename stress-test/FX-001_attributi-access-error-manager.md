# FX-001 — Il Controlling Manager non può aprire Configurazione → Attributi (errore di accesso)

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | Gap register: "COVERED = un utente lo fa da UI" · AD-115 (il manager configura) · SC-013 (passaggio 1) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_manager |
| DL collegato | — |

## Comportamento osservato
1. Login come Controlling Manager.
2. Controllo di gestione → Configurazione → Anagrafiche → Attributi.

→ Compare il dialog "Errore di accesso: Impossibile leggere il campo cmc.attribute.target_model_ids / Accesso ai record "Modelli" (ir.model) non consentito. L'operazione è consentita per i seguenti gruppi: - Diritti di accesso". La pagina resta sulla home e la barra menu sparisce.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-013_screenshot.png` · `…_trace.zip` · `…_console.txt`

## Comportamento atteso
> Il Manager configura le anagrafiche della suite (AD-115). Una voce di menu visibile a un ruolo deve aprirsi per quel ruolo.

È l'unica voce su 114 che fallisce nel giro completo dei menu.

## AC di regressione
- **DATO** un utente con il solo gruppo Controlling Manager (non amministratore di sistema)
- **QUANDO** apre Configurazione → Attributi, crea un attributo e sceglie a quali documenti si applica
- **ALLORA** lista e form si aprono, la scelta dei documenti è possibile e il salvataggio riesce, senza errori di accesso

## Note per il developer
Il manager deve poter vedere e scegliere i tipi di documento a cui si applica un attributo, senza avere i diritti di amministratore di sistema.

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-053 · PASS
