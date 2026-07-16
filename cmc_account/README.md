# CMC – Integrazione Contabile (cmc_account)

Modulo **ponte** della suite CMC. Dipende da `cmc_base` e `account`.

## Obiettivo

Alimentare il controllo di gestione con i **dati reali della contabilità**: le righe
analitiche generate dalla co.ge. vengono **classificate automaticamente** secondo la
tassonomia CMC (comportamento, diretto/indiretto, elemento di costo), così da entrare
nella base di costo senza doppia imputazione.

![Regole di classificazione](static/description/class_rule.png)

## Configurazione (step-by-step)

1. Installa `cmc_account` (richiede `cmc_base` + `account`).
2. **Mappatura conto → elemento**: apri un conto (*Contabilità › Configurazione › Piano
   dei conti*) e valorizza **Elemento di costo CMC** per assegnare la natura del costo.
3. **Regole di classificazione**: *Controllo di gestione › Configurazione › Integrazione
   contabile › Classification Rules* → **Nuovo**.
   - **Match** (lascia vuoto = qualsiasi): conto, prefisso codice conto, categoria
     prodotto, partner, centro.
   - **Assegna** (solo fill): comportamento (fisso/variabile), diretto/indiretto,
     elemento di costo.
   - L'ordine (**sequence**) determina la priorità: la prima regola che combacia vince.

## Utilizzo (step-by-step)

1. Registra normalmente in contabilità con conto analitico: alla creazione della riga
   analitica scatta l'**auto-classificazione** (`_cmc_auto_classify`) secondo le regole.
2. **Verifica gli scoperti**: *Operazioni › Analisi & controllo › Unclassified Cost Lines*
   elenca le righe non ancora classificate — aggiungi/riordina le regole finché la lista
   si svuota.
3. Le righe classificate confluiscono automaticamente nella base di costo di `cmc_base`
   (registrazione costi, ribaltamento, quadro analisi).

## Note

- La classificazione manuale (`cmc_classified_manually`) ha la precedenza e non viene
  sovrascritta dalle regole.
- Base per i ponti operativi `cmc_hr_timesheet` (manodopera da timesheet) e `cmc_mrp`
  (costo primo da produzione), che ne estendono l'auto-classificazione.
