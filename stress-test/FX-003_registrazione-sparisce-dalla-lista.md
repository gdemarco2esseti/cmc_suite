# FX-003 — Le registrazioni di costo senza "comportamento" spariscono dalla lista ma restano nei conti

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r1)

| Campo | Valore |
|---|---|
| Classe | BUCO SPEC (da approvare) |
| Gravità | alta |
| Tracciabilità | RF-UX-002 (adiacente) · RF-INT-012 (coda dei non classificati) · SC-028, SC-027, SC-029 (passaggio 1) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · ruolo controlling_user |
| DL collegato | DL-001 |

## Comportamento osservato
1. Registrazioni di costo → Nuovo: centro Manutenzione, Costo 50, descrizione "SC028 no comportamento", comportamento vuoto → Salva.
2. Il record è visibile subito. Dopo il reload la lista è vuota ("Registra un costo") e cercando "SC028" non si trova nulla.

→ Lo stesso succede per SC027 (100), SC029 (−500) e SC029 (99.999.999.999). L'utente non ha modo di ritrovare e correggere questi record dal menu dove li ha creati. I record restano però nel DB come righe di costo del centro.

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r1/SC-028_*`

## Comportamento atteso (proposto, da approvare con DL-001)
Una registrazione di costo creata dall'utente non deve mai sparire dal menu in cui è stata creata. Due alternative:
- (a) il comportamento è obbligatorio al salvataggio;
- (b) la registrazione resta visibile in lista, segnalata come "da classificare".

## AC di regressione
- **DATO** una registrazione di costo appena salvata dal menu Registrazioni di costo
- **QUANDO** l'utente ricarica la lista o cerca la descrizione
- **ALLORA** la registrazione è presente, oppure il salvataggio era stato rifiutato con un messaggio sul campo mancante

## Note per il developer
Danno: un importo che entra nei costi del centro senza che l'utente possa vederlo o correggerlo (criterio 2, "dati persi senza conferma").

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-055 · PASS
