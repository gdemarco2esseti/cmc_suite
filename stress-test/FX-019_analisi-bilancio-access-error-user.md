# FX-019 — Analisi di bilancio: errore di accesso aprendo il form senza diritti contabili

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | alta |
| Tracciabilità | RF ruoli (sez. 3: lo User consulta le analisi) · trovato da odoo-ux-tester durante la delivery fix-stress-2026-10 · SC-071 |
| Bersaglio | local · Odoo 19.0+e · DB cmc_fx_merge / cmc_stress_r3 · ruolo controlling_user senza "Mostra funzioni contabili" |
| DL collegato | — |

## Comportamento osservato
Un controller senza diritti contabili apre una scheda di Analisi di bilancio e riceve "Errore di accesso" su `account.journal`. Lo causa il campo Registri esclusi (`excluded_journal_ids`) del form.

## Comportamento atteso
Il form si apre per ogni ruolo CMC. Il campo dei registri esclusi si vede solo con i diritti contabili.

## Correzione
`excluded_journal_ids` con `groups="account.group_account_readonly"` a livello di modello, non solo di vista: con il gruppo solo nella vista il campo restava nella descrizione dei campi di get_views. In più `copy=False` e un override di `copy()` in sudo, così la copia conserva i registri anche se a farla è un utente senza diritti contabili.

## AC di regressione
- **DATO** un utente Controllo di gestione / Utente senza diritti contabili
- **QUANDO** apre una scheda di Analisi di bilancio
- **ALLORA** il form si apre senza errori di accesso e il campo Registri esclusi non compare

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-071 · PASS
