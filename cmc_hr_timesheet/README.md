# CMC – Manodopera da Timesheet (cmc_hr_timesheet)

Modulo **ponte** della suite CMC. Dipende da `cmc_account` e `hr_timesheet`.

## Obiettivo

Portare la **manodopera diretta (MOD)** consuntiva dai **timesheet** nella base di costo
del controllo di gestione, con classificazione automatica come costo diretto di
manodopera. Utile anche come sorgente del **consuntivo** per lo scenario timesheet di
`cmc_tdabc`.

## Configurazione (step-by-step)

1. Installa `cmc_hr_timesheet` (richiede `cmc_account` + `hr_timesheet`).
2. Assicurati che i dipendenti abbiano un **costo orario** impostato (*Dipendenti ›
   scheda › costo*), così le righe di timesheet valorizzino l'importo.
3. Verifica/estendi le **regole di classificazione** (`cmc_account`) per intercettare le
   righe di timesheet e assegnarle all'elemento **Manodopera diretta** (comportamento e
   direttezza corretti).

## Utilizzo (step-by-step)

1. I dipendenti registrano le ore normalmente (timesheet su progetti/attività con conto
   analitico).
2. Ogni riga di timesheet riconosciuta come MOD (`_cmc_is_timesheet_mod`) viene
   **auto-classificata** ed entra nella base di costo come manodopera diretta.
3. I costi confluiscono nel ribaltamento e nelle metodologie di costing di `cmc_base` e
   dei moduli capillari; per il TDABC possono taggare l'**Attività TDABC** e alimentare
   lo scenario *Consuntivo (timesheet)*.

## Note

- Ponte "invisibile": non aggiunge menu propri, estende `account.analytic.line`.
- Nessun doppio conteggio: la riga di timesheet è già una riga analitica, viene solo
  classificata secondo la tassonomia CMC.
