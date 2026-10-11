# Scenari — CMC suite completa — run 2026-10-10_cmc-suite_r1

Oracolo: ⚠️ **PARZIALMENTE TRACCIATO**. Non esistono FS-NNN con AC DATO/QUANDO/ALLORA. Si usano come oracolo:
- RF della BA (`../01_*`, `../03_*`, `../04_*`);
- criteri di accettazione delle tech-specs TS-* (`../02_*`, `../05_*`, `../07_*`, `../08_*`);
- decisioni architetturali AD-*;
- messaggi di guardia documentati;
- gap register (`docs/analysis/gap-manuali-2026-10/10_gap_register.md`).

Bersaglio: local · http://localhost:8774 · DB `cmc_stress`. Il DB contiene i dati demo Odoo e il seed CMC: closing "Chiusura Gennaio", costing "Costo pieno Gennaio", "ABC Gennaio", "Ripartizione congiunti" e "TDABC Gennaio", tutti calcolati. Interfaccia in italiano.

## Ruoli

Tutti gli utenti appartengono alla sola azienda "My Company (San Francisco)", lingua it_IT.

| Ruolo | Login | Gruppi |
|---|---|---|
| controlling_user | qa_cmc_user | Controlling User |
| controlling_responsible | qa_cmc_resp | Controlling Responsible |
| controlling_manager | qa_cmc_mgr | Controlling Manager |
| no_cmc | qa_no_cmc | Utente interno; Contabilità: Fatturazione; Timesheet: utente. Nessun gruppo CMC |

## Pass 1

### Happy path

| SC | Fonte | Ruolo | Precondizioni | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|---|
| SC-001 | RF-UX-007/008, cruscotto | controlling_user | seed | Controllo di gestione → Cruscotto | Si apre il cruscotto con barra step e KPI, senza errori in console né traceback. | alta |
| SC-002 | RF-UX-002, AD-005, TS-001 | controlling_user | — | Operazioni → Base di costo → Registrazioni di costo → Nuovo. Centro "Manutenzione", descrizione "SC002 test", Costo 1234,56, elemento e comportamento qualsiasi. Salva e torna alla lista. | Il record è in lista con Costo 1.234,56 (positivo). | alta |
| SC-003 | RF-CDC-003/004, TS-004, AD-001 | controlling_manager | seed | Chiusure → Nuovo. Periodo 01/02/2026–28/02/2026, metodo step-down, centri/ordine come "Chiusura Gennaio" → Calcola. | Stato Calcolato. Matrice bilanciata pre = post (RF-REP-001). Centri ausiliari a zero. | alta |
| SC-004 | RF-FC-001..003, TS-005/006 | controlling_user | SC-003 calcolata | Costo pieno → Elaborazioni di costing → Nuovo. Closing di SC-003, periodo febbraio → Precompila righe → Calcola. | Calcolato. Risultati sui 4 livelli (primo, industriale, complessivo, economico-tecnico) visibili. | alta |
| SC-005 | TS-CVR-001 T1 | controlling_user | — | Direct costing e decisioni → CVR / punto di pareggio → Nuovo, modalità simulazione: prezzo 10, costo variabile unitario 4, costi fissi 90.000 → Ricalcola. | Q* = 15.000; fatturato di pareggio = 150.000. | alta |
| SC-006 | RF-ABC-001..003 | controlling_user | seed | Activity-Based Costing → Calcolo ABC → apri "ABC Gennaio" → leggi i risultati. Poi Nuovo con periodo febbraio → Precompila archi dai fatti → Calcola. | La lettura dei risultati funziona. Il nuovo run viene calcolato oppure dà un messaggio d'errore comprensibile, mai un traceback. | alta |
| SC-007 | RF-CGN-001/002 | controlling_user | seed | Costi congiunti → Ripartizione congiunti → Nuovo. Costo congiunto 10.000, due prodotti principali (qty 100 e 300), metodo quantità fisica → Calcola. | Ripartizione 2.500 / 7.500. Somma = 10.000. | alta |
| SC-008 | TDABC | controlling_user | seed | Time-Driven ABC → Calcolo TDABC → apri "TDABC Gennaio" → controlla capacità. | Costo assorbito + costo capacità inutilizzata = costo risorsa, per ogni risorsa. | media |
| SC-009 | budget, AD-115 | controlling_manager | — | Base di costo → Versioni di piano → Nuovo, anno 2026 → Approva. Budget e scostamenti → Righe di budget → Nuovo: centro "Manutenzione", data 15/03/2026, importo 5.000. | La versione passa ad Approvata. La riga di budget è salvata. "Budget vs consuntivo" si apre senza errori. | alta |
| SC-010 | job costing / EVM | controlling_manager | — | Costing di commessa → Elaborazioni costo di commessa → Nuovo (periodo marzo 2026) → Calcola. Poi Baseline → Nuovo → Approva. | Run calcolato oppure messaggio comprensibile (es. "nessuna tariffa"). Baseline approvata. Nessun traceback. | media |
| SC-011 | KPI | controlling_manager | — | KPI e scheda di valutazione → Libreria KPI → Nuovo, codice `sc011_kpi`, rosso 10, ambra 20 → Salva. Poi Esecuzioni di misurazione → Nuovo → Calcola. | KPI salvato. Esecuzione calcolata oppure messaggio comprensibile. | media |
| SC-012 | profitability | controlling_user | ABC Gennaio calcolato | Redditività → Esecuzioni di redditività → Nuovo, periodo gennaio 2026 → Calcola → Margini clienti. | Run calcolato oppure messaggio comprensibile. Le viste dei margini si aprono senza traceback. | media |
| SC-013 | menu sweep | controlling_manager | — | Apri, una per una, **tutte** le voci di menu sotto Controllo di gestione (Operazioni, Responsabilità, Dashboard, Configurazione) in vista lista/default. | Nessuna voce produce traceback o dialog di errore. Annotare ogni voce che fallisce, con il nome. | alta |

### Permessi

| SC | Fonte | Ruolo | Precondizioni | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|---|
| SC-014 | P0 review 09-17, regressione | no_cmc | — | Timesheet → Nuovo: progetto qualsiasi, 2 ore → Salva. Poi Contabilità → Fatture clienti → Nuovo, una riga → Conferma. | Entrambe le operazioni riescono senza AccessError. | bloccante |
| SC-015 | AD-115 | no_cmc | — | Cerca il menu "Controllo di gestione". Poi incolla l'URL dell'azione Chiusure (copiata dalla sessione manager) e aprila. | Il menu non è visibile. L'URL dà un messaggio d'accesso negato pulito, non un traceback. | alta |
| SC-016 | AD-115 | controlling_responsible | — | Login. Apri Responsabilità → Centri di responsabilità, Costo per centro, I miei KPI. Poi prova a modificare un centro di responsabilità. | I menu si aprono. La modifica non è possibile (form readonly o messaggio d'accesso), mai un traceback. Le voci Operazioni e Configurazione non sono visibili. | alta |
| SC-017 | AD-115, ACL 1110 | controlling_user | SC-003 | Chiusure → seleziona un run in lista → Azioni → Elimina. | Elimina assente oppure messaggio d'accesso comprensibile. Il run resta. | alta |
| SC-018 | AD-115 | controlling_user | SC-009 (una versione in bozza) | Versioni di piano → apri una versione in bozza → cerca il bottone Approva. | Approva non visibile all'utente. | alta |
| SC-019 | ACL master 1000 | controlling_user | — | Configurazione non visibile. Apri un elemento di costo da una registrazione (link m2o) e prova a modificarlo. | Readonly oppure messaggio d'accesso. Nessuna modifica salvata. | media |
| SC-020 | TS-010, RF-CFG-002 multi-company | controlling_user | Admin (passo di precondizione, sessione admin) crea in "My Company (Chicago)" una Chiusura in bozza "SC020 Chicago" e annota l'id. | Come qa_cmc_user apri `/odoo/action-…/<id>` (URL del record Chicago) e cerca "SC020" nella lista Chiusure. | Record non visibile in lista. L'URL dà un messaggio d'accesso/record mancante, mai i dati. | bloccante |

### Interazione e stato

| SC | Fonte | Ruolo | Precondizioni | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|---|
| SC-021 | AD-001, checklist interazione | controlling_user | Run costing in bozza (nuovo, con righe precompilate) | Doppio clic rapido su Calcola. | Un solo calcolo: risultati non duplicati (conteggio righe risultato = quello di un singolo calcolo). Al massimo un messaggio "già calcolato". | alta |
| SC-022 | AD-001 | controlling_user | SC-004 calcolato | Modifica una riga di input del run calcolato e salva. | Messaggio "Reset it to draft before changing its inputs" (o la traduzione), nessun salvataggio. | alta |
| SC-023 | AD-002 | controlling_manager | SC-003 + SC-004 calcolati | Apri la closing di SC-003 → Reimposta a bozza. | Bloccato con un messaggio che nomina il costing calcolato. Nessun reset a cascata. | alta |
| SC-024 | AD-001 reset | controlling_manager | SC-004 | Costing di SC-004 → Reimposta a bozza → conferma → Calcola di nuovo. | Dopo il reset i risultati sono vuoti. Dopo il ricalcolo i totali sono identici al primo calcolo (idempotenza). | alta |
| SC-025 | checklist stato, CB-8 | controlling_user | — | Due tab sullo stesso run ABC in bozza. Tab 1 → Calcola. Tab 2 (non ricaricato) modifica il nome/una riga → Salva. | Il tab 2 riceve un messaggio di blocco o di conflitto. Lo stato non torna a bozza e i risultati non si corrompono. | media |
| SC-026 | AD-001, reset senza conferma | controlling_user | — | Direct costing e decisioni → Decisioni → Nuovo → Calcola (se possibile) → Reimposta a bozza. | Annotare se il reset avviene senza dialog di conferma (incoerente con gli altri run) e se i risultati spariscono. | bassa |

### Dati

| SC | Fonte | Ruolo | Precondizioni | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|---|
| SC-027 | RF-UX-002 | controlling_user | — | Registrazioni di costo → Nuovo: Costo 100, descrizione "SC027 senza centro", **nessun centro** → Salva. | Salvataggio rifiutato con un messaggio sul centro. | alta |
| SC-028 | lista domain cmc_behavior | controlling_user | — | Registrazioni di costo → Nuovo: centro "Manutenzione", Costo 50, descrizione "SC028 no comportamento", comportamento **vuoto** → Salva → torna alla lista e cerca "SC028". | La registrazione è visibile, oppure il salvataggio è bloccato con un messaggio. Il record non deve sparire in silenzio. | alta |
| SC-029 | RF-CGN, guard base≤0 | controlling_user | — | Nuova ripartizione congiunti: costo 10.000, metodo valore di vendita, prezzi a 0 → Calcola. Poi registrazione di costo con Costo −500 e con 99999999999. | Un messaggio comprensibile per base ≤ 0. Gli importi estremi vengono rifiutati o salvati coerentemente, senza traceback. | media |
| SC-030 | AD-005 | controlling_user | — | Registrazione di costo: compila sia "Importo" sia "Costo" (se entrambi visibili) → Salva. | Messaggio "Set either Amount or Cost, not both" (o la traduzione). | bassa |

## Scartati (cap ~30)

| Categoria | Scenario | Motivo |
|---|---|---|
| happy path | cmc_process_costing, cmc_standard_cost (roll-up/push), cmc_abb, cmc_scenario, cmc_fin_analysis, cmc_target_lcc, ponte cmc_mrp | Non c'è seed e serve setup pesante da UI (BoM, work center, versioni approvate, bilanci). La copertura parziale è data da SC-013 (menu sweep). Candidati per il pass 2. |
| contesto | lingua en_US, viewport mobile | Priorità 6. |
| interazione | rete offline al salvataggio | Priorità bassa, comportamento standard del client web. |
| dati | unicode/testo lungo nelle descrizioni | Priorità 5, basso rischio. |

## Pass 2 — fonte: council

Verdetto del council in sintesi. Quattro revisori su cinque hanno indicato come risposta più forte quella del First Principles: i motori hanno retto, quindi i difetti stanno **nei passaggi tra un modulo e l'altro**. In particolare:
- il segno lungo la catena;
- il reset di un'origine sotto un consumatore già calcolato;
- le azioni che scrivono fuori da CMC (push prezzi, MRP);
- gli utenti con più aziende attive e i responsabili che vedono aggregati.

I cinque advisor hanno convergenze nette su tre punti: cancellazione delle righe col Precompila, scheda vecchia sui run privi di guardia (CB-8), reset senza conferma. I revisori aggiungono due lacune: l'export XLSX/PDF che potrebbe aggirare le regole di visibilità e le anagrafiche archiviate dopo l'uso.

Ogni scenario parte da dati creabili da UI in pochi minuti e dichiara il valore atteso.

| SC | Motivazione del council | Ruolo | Precondizioni | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|---|
| SC-031 | 5/5 advisor: Precompila svuota le righe manuali? | controlling_user | — | Costo pieno → Elaborazioni di costing → Nuovo. Closing "Chiusura Febbraio", periodo febbraio. Aggiungi 2 righe driver a mano (Montaggio Z / un prodotto / 10; Montaggio Y / un prodotto / 5) → Salva → Precompila righe → riapri il record. | Le 2 righe manuali ci sono ancora, oppure prima della sostituzione compare una conferma esplicita. Se non ci sono fatti, un messaggio lo dice. | alta |
| SC-032 | catena del segno (D1, A3, B6, E1) | controlling_manager → controlling_user | Manager: Versioni di piano → Nuovo "Budget SC032" 2026 in bozza; riga di budget Montaggio Z, 15/03/2026, 5.000 → Approva. | User: Registrazione di costo, Montaggio Z, 10/03/2026, Costo 1.000, comportamento qualsiasi. Poi Chiusure → Nuovo marzo 2026 (step-down, come Febbraio) → Calcola. Poi Budget vs consuntivo, filtro Montaggio Z / marzo. | La chiusura mostra 1.000 su Montaggio Z con lo stesso segno dei costi di gennaio. In BvA consuntivo 1.000 e budget 5.000 hanno lo **stesso segno** e lo scostamento è 4.000, presentato come favorevole. Annotare i segni esatti visti. | alta |
| SC-033 | reset di un'origine sotto un consumatore calcolato, provato solo sulla closing (D3, B1) | controlling_user → controlling_manager | "ABC Febbraio" calcolato (01/02–28/02). | User: Redditività → Esecuzioni di redditività → Nuovo, periodo febbraio 2026, ABC = ABC Febbraio → Calcola. Manager: apri ABC Febbraio → Riporta in bozza. | Reset bloccato con un messaggio che nomina l'esecuzione di redditività. Se il reset passa, annotare cosa mostra la redditività (numeri vecchi? errore?). | alta |
| SC-034 | scheda vecchia su un run senza guardia (CB-8). SC-025 aveva provato solo il nome | controlling_user | Costing in bozza con almeno una riga driver e calcolabile (es. quello di SC-031, completato a mano). | Scheda A: apri il run in bozza. Scheda B: stesso run → Calcola. Scheda A (non ricaricata): modifica la quantità di una riga driver (es. 10 → 99) → Salva. Ricarica la scheda B. | Il salvataggio nella scheda A è rifiutato ("Riporta in bozza prima di modificare…"). In B la riga resta 10 e i risultati non cambiano. Se passa: FAIL, annotare se i risultati sono ormai incoerenti con la riga. | alta |
| SC-035 | push prezzi: effetto fuori CMC e per azienda (E3, B4) | controlling_manager (+ admin per controllare l'inventario) | Admin: annota il costo del prodotto "Table" in Inventario, nell'azienda SF e in Chicago. | Manager: Costo standard → crea una versione standard (Versioni di piano, tipo standard se richiesto) → Precompila tariffe → Calcola schede (Roll Up Cards) → Approva → Push Standard Prices (conferma). Admin: ricontrolla il costo di "Table" in SF e in Chicago. Manager: prova a riportare in bozza o annullare la versione. | Il costo di Table cambia **solo** in SF, al valore della scheda. Chicago resta invariata. Una sola modifica (nessun doppio push). Dopo il reset il prezzo torna indietro, oppure un messaggio chiaro dice che il push non è reversibile. In caso di BLOCKED annotare il passo esatto. | alta |
| SC-036 | aggregati con due aziende attive (D7) | admin | Admin attiva SF + Chicago nel selettore. Crea in Chicago un centro di costo finale "SC036 Chicago" e una registrazione di costo 777, data 12/03/2026. | Come admin, con entrambe le aziende attive, apri la chiusura di marzo SF (SC-032) → Riporta in bozza → Calcola. Poi Quadro analisi costi / Costo per centro. | I totali di marzo SF non contengono 777. Il centro Chicago non compare nei risultati SF. A fine scenario ripristina il selettore a solo SF. | bloccante |
| SC-037 | perdita sugli aggregati e sull'export per il Responsible (D8, revisori export) | controlling_manager → controlling_responsible | Manager: crea il centro di responsabilità "CdR SC037 altrui" con responsabile qa_cmc_mgr. "CdR SC016" ha come responsabile qa_cmc_resp (se non lo ha, impostalo). | Responsible: Responsabilità → Costo per centro e Centri di responsabilità → seleziona tutto → Azioni → Esporta (xlsx), più I miei KPI in vista pivot. | Vede ed esporta **solo** CdR SC016. Nel file e nei totali del pivot non compare nulla di "SC037 altrui". | bloccante |
| SC-038 | ponte MRP mai provato (C8, B7, D6) | admin → controlling_user | Admin: Produzione → Nuovo MO, prodotto "Table" (BoM demo), qty 1, centro CMC = Montaggio Z (se il campo c'è) → Conferma → Produci tutto → Fatto. Annota il costo dei componenti. | User: Registrazioni di costo / Quadro analisi, filtro Montaggio Z e data di oggi. | Costo materiali dell'MO presente **una sola volta** (niente doppio conteggio) e pari al costo dei componenti, con il segno di un costo. Le righe MOD, se ci sono, sono classificate come manodopera. | alta |
| SC-039 | ponte timesheet: segno e classificazione MOD (B6, E10) | admin → no_cmc → controlling_user | Admin: progetto "SC039" con conto analitico = Montaggio Z (finale). Dipendente di qa_no_cmc con costo orario 50. | no_cmc: foglio ore 2 h su SC039. User: Registrazioni di costo, filtro Montaggio Z. | Riga di costo 100 (positiva nel campo Costo), classificata come MOD/diretta (centro finale). Nessun errore d'accesso per no_cmc. | alta |
| SC-040 | anagrafica archiviata dopo l'uso, export e stato vuoto (revisori) | controlling_manager → controlling_user | — | Manager: archivia il centro "Commerciale" (usato da Chiusura Gennaio). User: apri Chiusura Gennaio → risultati → Esporta xlsx. Poi, con un utente nuovo nel percorso (user), apri CVR / Budget vs consuntivo / Redditività filtrando un periodo senza dati (2027). | La chiusura storica mostra ancora Commerciale con i suoi importi. Il file esportato ha gli stessi totali dello schermo. Le viste vuote mostrano un helper o "nessun dato", mai un traceback. | media |

Esclusi dal council: Monte Carlo 0/1M iterazioni; en_US; disinstallazione dei ponti; concorrenza tra due utenti; scala. Motivo: cap di 10 scenari; per la disinstallazione, non è un'azione da utente.
