# Scenari — CMC suite, rilancio 2 — run 2026-10-11_cmc-suite_r2 (LIGHT)

| Voce | Valore |
|---|---|
| Tipo | Rilancio degli FX rimasti aperti dopo `2026-10-11_cmc-suite_r1`: FX-006, FX-009, FX-010, FX-012, FX-016, FX-018, FX-020, DL-010, più la nota SC-060 e le obiezioni ronin RN-007/RN-008 |
| Bersaglio | local · http://localhost:8776 · DB `cmc_stress_r3`, aggiornato a `fix/stress-2026-10` con `--i18n-overwrite` |
| Ruoli | `qa_cmc_user`, `qa_cmc_mgr`, `qa_cmc_resp` (già creati nel r1), `admin` per le precondizioni |

## Scenari

| SC | Fonte | Ruolo | Passi da utente | Atteso a schermo | Gravità |
|---|---|---|---|---|---|
| SC-074 | FX-006 | user | Ripartizione congiunti, metodo "Valore di vendita al punto di separazione" con prezzi 0 → Calcola. Poi Target, LCC, Confronto scenari, Simulazione e Budget what-if: leggi l'etichetta del bottone di reset su un record calcolato. | Il messaggio nomina le colonne da compilare sulle righe dei prodotti principali. Il bottone si chiama "Riporta in bozza" ovunque. | bassa |
| SC-075 | FX-009 | manager | Budget vs consuntivo con le impostazioni predefinite. Poi togli il filtro "Ultima versione approvata". | Il report si apre filtrato e raggruppato per versione. Senza filtro, all'interno di ogni versione il consuntivo di un centro e di un mese è quello reale (Delivery Advisory gennaio 2026 = 27.408), non raddoppiato. L'help del consuntivo spiega che si ripete per ogni versione. | alta |
| SC-076 | FX-010 | admin → manager | Admin: distinta base di produzione (tipo "Produzione del prodotto", senza operazioni) per un prodotto di Delta, con un componente a costo noto. Manager: versione di piano Standard → roll-up delle schede su quel prodotto → approva → Trasferisci prezzi standard → anteprima → Conferma → prova Riporta in bozza. | Prima del push compare l'anteprima prodotto/costo vecchio → nuovo. Dopo il push il reset viene bloccato con un messaggio. I prezzi trasferiti si vedono nella versione e non sono modificabili. | alta |
| SC-077 | FX-020 | manager | Versione Standard in bozza → roll-up delle schede su un prodotto senza distinta di produzione (per esempio "Table Kit"). | Messaggio che nomina il prodotto e il percorso Produzione > Prodotti > Distinte base. Nessuna scheda creata. | media |
| SC-078 | FX-012 | user | Costing di commessa → Curva a S. | Grafico a linee non in pila con PV, EV e AC. Dopo la data di stato EV e AC restano piatte e non scendono a 0. PV continua fino a fine progetto. | media |
| SC-079 | FX-016 | manager | Versione budget in bozza → Aggiungi budget annuale: con costo fisso guarda se c'è il campo stagionalità. Poi con un costo variabile, stagionalità "Stagionalità servizi", 12000 → Aggiungi. | Con costo fisso il campo stagionalità non c'è. Con costo variabile le 12 righe seguono il profilo (marzo-maggio più alti, luglio più basso) e sommano 12.000. | media |
| SC-080 | FX-018 + RN-007 + RN-008 | manager | KPI → Misurazioni: riga ROS. KPI ROS → form: obiettivo, scrivi 15 → salva. Analisi di bilancio → Benchmark di settore: valore del ROS. Valori degli indici: intestazioni. Stampa il pacchetto direzionale di una misurazione. | ROS in % sia in Misurazioni sia negli Indici, obiettivo compreso. Nel form l'obiettivo si legge e si scrive in percentuale (15 → 15 %). Il benchmark si inserisce in percentuale. Intestazioni in italiano. Nel PDF il ROS è in %. | bassa |
| SC-081 | DL-010 | user | KPI → Misurazioni: Riporta in bozza e Calcola "Video Misurazione Q4 2026" → "Unità al primo passaggio". | Stato "Nessun dato", con un motivo che nomina gli input senza dati. Non "In linea". | media |
| SC-082 | SC-060 nota | responsible | Apri il cruscotto CMC dall'URL diretto (action del cruscotto). | Nessun errore di accesso. Il grafico per elemento non compare. | bassa |
