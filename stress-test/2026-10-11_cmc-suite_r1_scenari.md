# Scenari — CMC suite, rilancio dopo i fix — run 2026-10-11_cmc-suite_r1 (LIGHT)

| Voce | Valore |
|---|---|
| Tipo | Rilancio di FX-001..FX-019 (§ Rilancio di ba-stress-test): AC di regressione degli FX + happy path delle aree toccate. |
| Bersaglio | local · http://localhost:8776 · DB `cmc_stress_r3`. È una copia di `cmc_video` (azienda "Delta Servizi S.r.l." con dati demo) aggiornata al codice di `fix/stress-2026-10`. |
| Ruoli | `qa_cmc_user`, `qa_cmc_mgr`, `qa_cmc_resp`, da creare nell'azienda Delta Servizi, in italiano. |

## Scenari

| SC | Fonte | Ruolo | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|
| SC-053 | FX-001 AC | manager | Configurazione → Attributi → Nuovo: nome, codice, "Si applica a" = Contatto → Salva. | Lista e form si aprono senza errori di accesso. L'attributo viene salvato. | alta |
| SC-054 | FX-002 AC | user | Registrazioni di costo → Nuovo: Costo 100, senza centro → Salva. Poi con il centro ma senza comportamento → Salva. | Entrambi i salvataggi vengono rifiutati: campi obbligatori segnalati, nulla registrato. | alta |
| SC-055 | FX-003 AC + RN-001 | user | Registrazioni di costo: filtro "Da classificare". Confronta con la lista completa. | Le righe di costo senza comportamento sono visibili nel filtro. Le righe di ricavo (fatture clienti) non compaiono tra le registrazioni di costo. | alta |
| SC-056 | FX-004 AC | user | Costo pieno → Nuovo: 2 righe driver a mano → Salva → Precompila righe. | Prima compare una conferma. Senza dati compare un messaggio con il percorso dove inserirli, e le 2 righe restano. | alta |
| SC-057 | FX-005 | user | Direct costing → CVR, vista lista. | La colonna "Esiste il pareggio" è leggibile: niente HTML. | media |
| SC-058 | FX-006 | user | Ripartizione congiunti, metodo "valore di vendita" con prezzi 0 → Calcola. Redditività con un ABC senza date → Calcola. | I messaggi usano le etichette che l'utente vede (niente "sales_value"), niente "ABC ABC", e dicono cosa correggere. | bassa |
| SC-059 | FX-007 | user | Decisioni, Scenari, Target/LCC: su un record calcolato, Riporta in bozza. | Compare la conferma "Riportando in bozza i risultati calcolati vengono cancellati. Continuare?". Con Annulla i risultati restano. | media |
| SC-060 | FX-008 AC | responsible | Login → apri l'app Controllo di gestione → Centri di responsabilità. | Nessun errore di accesso. Archivia non viene proposto. | alta |
| SC-061 | FX-009 AC + RN-003 | manager | Budget vs consuntivo con le impostazioni predefinite. Poi raggruppa per versione. | Si apre filtrato sulla versione approvata che copre il mese corrente, con il nome visibile. Budget e consuntivo stanno sulla stessa riga, i costi sono positivi e lo scostamento favorevole è verde. Senza filtro, il consuntivo di un centro e di un mese non è raddoppiato. | alta |
| SC-062 | FX-010 AC + RN-002 | manager | Costo standard: versione approvata con schede → Trasferisci prezzi standard. Nell'anteprima confronta i costi vecchio → nuovo → Conferma. Poi prova Riporta in bozza. | Prima del push compare l'anteprima dei prodotti. Dopo il push il reset viene bloccato con un messaggio. I prezzi trasferiti si vedono nella versione e non sono modificabili. | alta |
| SC-063 | FX-011 AC | admin | Produzione → Nuovo MO: imposta "Centro di costo CMC" → completa l'MO. Poi, come user, apri le Registrazioni di costo del centro. | Il campo ha la sua etichetta. Il costo dei materiali compare una volta sola, con il valore dei componenti. | alta |
| SC-064 | FX-012 AC | user | Costing di commessa → Curva a S. | Grafico a linee con PV, EV e AC. Le serie non calano dopo la data di stato e più elaborazioni non vengono sommate. | media |
| SC-065 | FX-013 AC | user | Costing di commessa → Nuova elaborazione su un periodo con ore di commessa senza centro → Calcola. | Un solo messaggio che elenca le commesse con le relative ore e indica il piano analitico su cui impostare il centro. | media |
| SC-066 | FX-014 AC | user | Redditività: calcola un'esecuzione collegata a un ABC che non assegna costi a clienti o prodotti. | Messaggio chiaro che nomina l'ABC, invece di margini M1..M4 identici. | alta |
| SC-067 | FX-015 AC | user | ABB → Scostamenti: ricalcola (reset e calcolo) "Video scostamenti Q1" → risorsa Team consulenti. | Il costo effettivo è circa 121.800, non 0. | alta |
| SC-068 | FX-016 AC | manager | Versione di piano budget in bozza → Aggiungi budget annuale, con un profilo di stagionalità. | Il budget viene ripartito sui mesi secondo il profilo. | media |
| SC-069 | FX-017 | user | Statusbar dei run di ogni modulo e righe di chiusura (Tipo di centro). | Tutto in italiano: Bozza / Calcolato, Finale / Comune, Ausiliario. | bassa |
| SC-070 | FX-018 | manager | LCC, Analisi di bilancio, Valori degli indici raggruppati per area, ROS nei KPI e negli Indici, wizard ROI → Calcola. | Anni scritti "2026". Nessun totale su soglie o indici. ROS nella stessa unità in KPI e Indici. Il dialog mantiene il titolo. | bassa |
| SC-071 | FX-019 | user | Analisi di bilancio → apri il form di un'analisi. | Nessun errore di accesso. | alta |
| SC-072 | DL-010 | user | KPI → Misurazioni: KPI di produzione senza dati. | Stato "Nessun dato", non "In linea". | media |
| SC-073 | regressione: happy path della chiusura + costo pieno | manager | Chiusura di un mese con costi → Calcola. Costo pieno sullo stesso mese → Calcola. Poi reset del costing e ricalcolo. | Entrambi i run vengono calcolati. Il reset seguito dal ricalcolo dà gli stessi totali. | alta |
