# CMC – Costo Primo da Produzione (cmc_mrp)

Modulo **ponte** della suite CMC. Dipende da `cmc_account` e `mrp_account`.

## Obiettivo

Alimentare il **costo primo** (materie prime MP + manodopera di produzione MOD) dai
dati reali della **produzione MRP**, generando righe analitiche già classificate e
imputate al centro di costo di produzione.

## Configurazione (step-by-step)

1. Installa `cmc_mrp` (richiede `cmc_account` + `mrp_account`).
2. Sui **centri di lavoro** / ordini di produzione, associa il **Centro di costo CMC**
   (`cmc_center_id`) che deve assorbire il costo di produzione.
3. Verifica/estendi le **regole di classificazione** (`cmc_account`) perché i movimenti
   di materiale siano imputati all'elemento **Materie prime** e la manodopera a
   **Manodopera diretta**.

## Utilizzo (step-by-step)

1. Esegui la produzione normalmente (ordini MRP, consumi di componenti, registrazione ore).
2. Alla valorizzazione, il ponte crea/collega le **righe analitiche di costo primo**
   (`_cmc_create_material_lines`) per materiali e manodopera sul centro indicato,
   con auto-classificazione.
3. Le righe confluiscono nella base di costo di `cmc_base` e alimentano il full costing
   (costo primo → industriale) e le altre metodologie.

## Limiti noti

- **Nessuna scrittura di storno.** Le righe di costo MP vengono create alla
  valorizzazione dell'ordine (`_post_inventory`) e **non vengono stornate**: uno
  `mrp.unbuild` (disassemblaggio), un reso di componenti o uno scarto lasciano il costo
  materiali **permanentemente** sul centro. Finché la contropartita non è implementata,
  su questi scenari il costo del centro va rettificato a mano (riga analitica manuale di
  segno opposto). Il test `test_unbuild_does_not_reverse_material_cost_yet`
  (`tests/test_mrp_integration.py`) **fissa** il comportamento attuale: quando lo storno
  sarà implementato, l'asserzione finale passa da `-21.0` a `0.0`.
- **Manodopera contabilizzata due volte.** `mrp_account` registra il tempo dei centri di
  lavoro sia come riga analitica sull'ordine di lavoro sia come scrittura contabile sul
  conto di costo di produzione (`_post_labour`). Se quel conto ha una distribuzione
  analitica, nasce una **seconda** riga analitica per le stesse ore: il ponte la
  riconosce (tramite `mrp.workcenter.productivity.account_move_line_id`) e la **esclude**
  dalla classificazione, così non entra nella base di costo.

## Note

- Estende `mrp.production`, `stock.move` e `account.analytic.line`.
- Ponte "invisibile": non aggiunge menu propri; il legame è il **Centro di costo CMC**
  sull'ordine di produzione.
- Evita il doppio conteggio riconciliando i consumi MRP con le righe analitiche generate:
  se `stock_account` ha **già** creato una riga analitica sul centro (consumo con
  distribuzione analitica), il ponte la **classifica e la adotta** invece di crearne una
  parallela.
