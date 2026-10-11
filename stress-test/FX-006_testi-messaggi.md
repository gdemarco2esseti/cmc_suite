# FX-006 — Testi dei messaggi e delle etichette: codici tecnici, parole doppie, etichette incoerenti

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | ANOMALIA |
| Gravità | bassa |
| Tracciabilità | RF-UX-010 (messaggi azionabili) · SC-009, SC-011, SC-012, SC-029 (passaggio 1) · SC-040 (passaggio 2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress · lingua italiano |
| DL collegato | — |

## Comportamento osservato
- Costi congiunti, metodo valore di vendita con prezzi a 0: "Nessuna base di riparto positiva per il metodo 'sales_value'…". Compare il codice tecnico del metodo al posto dell'etichetta.
- Redditività: "L'esecuzione ABC ABC Gennaio deve rientrare nell'arco temporale dell'esecuzione." La parola ABC è doppia. Il messaggio inoltre non dice quale campo correggere (l'ABC Gennaio ha le date vuote).
- Versione di piano approvata: "Il calcolo Budget 2026 è concluso. Riportalo in bozza…". Una versione di piano non è un calcolo, e l'utente non capisce che deve riportare la versione in bozza.
- Stati vuoti: CVR e Redditività dei clienti senza dati mostrano una griglia vuota, senza il testo di aiuto che invece ha Budget vs consuntivo (SC-040).
- Il bottone di reset si chiama "Reimposta a bozza" in KPI e "Riporta in bozza" altrove.

## Comportamento atteso
I messaggi usano le etichette che l'utente vede a schermo, nominano l'oggetto con il suo tipo corretto e dicono cosa fare. Lo stesso bottone ha la stessa etichetta in tutta la suite.

## AC di regressione
- **DATO** ciascuno dei 4 casi sopra
- **QUANDO** l'utente riproduce l'azione
- **ALLORA** il messaggio non contiene codici interni né parole ripetute, e il bottone di reset ha un'unica etichetta in tutta la suite

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-058,SC-059 · PASS sui messaggi; restano due punti: il messaggio dei congiunti non diceva quale campo compilare (corretto dopo il run), e l'etichetta "Reimposta a bozza" su Target, LCC, Confronto, Simulazione e what-if (traduzione vecchia conservata dall'aggiornamento senza --i18n-overwrite). Da rilanciare.
- 2026-10-11_cmc-suite_r2 · SC-074 · PASS
