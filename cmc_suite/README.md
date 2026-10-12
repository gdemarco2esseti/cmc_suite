# CMC – Cost Management Suite (cmc_suite)

Modulo **ombrello** della suite CMC per Odoo 19 Community. Non aggiunge modelli propri:
dipende da tutti i moduli della suite e li installa in un unico passaggio.

## Obiettivo

Dare un **unico punto di installazione** per l'intera suite di controllo di gestione,
invece di installare i 9 moduli uno a uno. Utile per un ambiente demo/produzione dove
si vuole avere subito disponibili tutte le metodologie di costing e tutti i bridge dati.

Moduli inclusi:

| Modulo | Ruolo |
|---|---|
| `cmc_base` | core: tassonomia costi, centri, ribaltamento, capacità, cockpit |
| `cmc_abc` | Activity-Based Costing a due stadi |
| `cmc_tdabc` | Time-Driven ABC (capacity cost rate + time equations) |
| `cmc_full_costing` | costo pieno (4 configurazioni) + costi figurativi |
| `cmc_direct_costing` | direct costing, CVR/break-even, leva operativa |
| `cmc_joint_costs` | ripartizione costi congiunti e sottoprodotti |
| `cmc_account` | bridge contabilità → controllo di gestione (mapping + classificazione) |
| `cmc_hr_timesheet` | bridge costo del lavoro (MOD) da timesheet |
| `cmc_mrp` | bridge costo primo (MP+MOD) da produzione |

`cmc_hr_timesheet` e `cmc_mrp` portano con sé le rispettive dipendenze Odoo
(`hr_timesheet`, `mrp_account`): installando `cmc_suite` verranno installate anche loro.

## Configurazione (step-by-step)

1. Verifica che `custom` sia nell'`addons_path` (`config/odoo.conf`).
2. **App → Aggiorna elenco app**.
3. Cerca **"CMC - Cost Management Suite"** e clicca **Installa**.
4. Odoo installa a cascata tutti i moduli in tabella (incluso `hr_timesheet` e
   `mrp_account` se non già presenti).
5. Assegna il gruppo **CMC Manager** (`cmc_base.group_cmc_manager`) agli utenti che
   devono operare sulla suite (Impostazioni → Utenti).

## Utilizzo (step-by-step)

1. Apri l'app **CMC** dal menu principale: il **Cruscotto** (`cmc.cockpit`) mostra lo
   stato di avanzamento di ogni metodologia (chiusura, costing, ABC, TDABC, joint...).
2. Segui l'ordine consigliato dal cruscotto: **chiusura centri → costing/ABC/TDABC/joint
   → CVR/direct costing** per un ciclo di controllo di gestione completo sullo stesso
   periodo.
3. Per il dettaglio di configurazione e utilizzo di ciascuna metodologia, vedi il README
   del modulo specifico (`custom/cmc_*/README.md`).
4. Per popolare rapidamente un ambiente demo con dati coerenti su tutta la suite, esegui
   `scripts/cmc_seed_full.py` (vedi intestazione script per i parametri di connessione).

## Note

- `cmc_suite` è solo un aggregatore: disinstallarlo **non** rimuove i moduli dipendenti
  già installati (comportamento standard Odoo).
- Se non serve il bridge MRP o Timesheet, installa i moduli singolarmente invece di
  `cmc_suite` (evita di tirare dentro `hr_timesheet`/`mrp_account`).
