# FX-016 — Profili di stagionalità: si creano ma nessuna schermata li applica

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | BUG |
| Gravità | media |
| Tracciabilità | gap register BUD (budget mensilizzato, stagionalità): "COVERED = l'utente lo fa da UI" · VG-9 · SC-049 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_manager |
| DL collegato | — |

## Comportamento osservato
1. Configurazione → Profili di stagionalità → nuovo profilo "SC049 Stagionale" → salvato.
2. Nella versione di piano, nelle righe di budget e in "Suddividi valori annuali" non c'è alcun campo per scegliere il profilo. "Suddividi" su una versione di un anno risponde "copre un solo anno: non c'è nulla da suddividere".

→ Il profilo non si può usare. Verifica sul codice: il motore accetta un profilo, ma nessuna azione utente glielo passa.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-049_screenshot.png`

## Comportamento atteso
Nella versione di piano o nella riga di budget l'utente sceglie un profilo di stagionalità, e il costo variabile e i ricavi annuali vengono ripartiti sui mesi secondo i pesi del profilo. I costi fissi restano annuo/12 (BUD-02).

## AC di regressione
- **DATO** un profilo con peso 2 a dicembre e 1 negli altri mesi, e un ricavo annuo di 13.000
- **QUANDO** l'utente inserisce il budget annuo scegliendo quel profilo
- **ALLORA** dicembre vale 2.000 e ciascuno degli altri mesi 1.000

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-068 · FAIL: la stagionalità è ignorata per i costi fissi, che per regola BUD-02 si ripartiscono in parti uguali, ma il wizard la proponeva senza dirlo. Corretto: il campo si vede solo quando ha effetto (costi variabili e ricavi). Da rilanciare.
- 2026-10-11_cmc-suite_r2 · SC-079 · PASS
