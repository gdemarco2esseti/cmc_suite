# FX-018 — UX: anni come "2.026", totali di gruppo su soglie e indici, ROS in unità diverse, wizard ROI intitolato "Odoo"

Stato: chiuso (2026-10-11, run 2026-10-11_cmc-suite_r2)

| Campo | Valore |
|---|---|
| Classe | ANOMALIA |
| Gravità | bassa |
| Tracciabilità | RF-UX (leggibilità) · VG UX · SC-052 (r2) |
| Bersaglio | local · Odoo 19.0+e · DB cmc_stress_r2 (copia di cmc_video, azienda Delta Servizi) · ruolo controlling_manager |
| DL collegato | — |

## Comportamento osservato
- **Formato degli anni:** LCC "Anno di pareggio 2.029"; Analisi di bilancio "Anno da 2.025 / Anno a 2.026"; colonna Anno dei valori degli indici "2.025", "2.026".
- **Totali di gruppo:** nei valori degli indici raggruppati per area si sommano valori e soglie, per esempio Redditività (48) 3.205,38 / 33,00 e Liquidità (48) 159.769,01 / 192,80.
- **ROS:** nei KPI vale 0,9076 con obiettivo 0,6000 (frazione); negli Indici 0,91 con soglia 7,50 e stato OK. Le unità sono diverse fra le due viste.
- **Wizard "Costo controllabile e ROI":** dopo Calcola l'intestazione del dialogo diventa "Odoo".

Evidenze: `stress-test/evidenze/2026-10-10_cmc-suite_r2/SC-052_screenshot*.png`

## Comportamento atteso
- Gli anni si leggono "2026".
- Nessun totale di gruppo su soglie, indici o percentuali.
- Il ROS ha la stessa unità ovunque, e soglia e valore sono nella stessa unità.
- Il dialogo mantiene il proprio titolo dopo il calcolo.

## AC di regressione
- **DATO** le viste citate
- **QUANDO** l'utente le apre e raggruppa per area
- **ALLORA** gli anni sono senza separatore, i gruppi non mostrano somme di soglie e indici, il ROS è espresso nella stessa unità in KPI e Indici, e il dialogo ROI conserva il titolo

## Rilancio
- 2026-10-11_cmc-suite_r1 · SC-070 · FAIL sul ROS: KPI 0,9076 contro Indici 90,76 %, benchmark 750 % (inserito come 7,5 su un indice salvato in frazione). Intestazioni Value/Threshold in inglese (DB non ancora aggiornato alle etichette nuove). Corretto: valori KPI in percentuale e benchmark inseriti in percentuale. Da rilanciare.
- 2026-10-11_cmc-suite_r2 · SC-080 · PASS sull'AC (ROS in % in KPI e Indici, obiettivo compreso; intestazioni in italiano). Resta cosmetico: i campi di inserimento in percentuale (obiettivo KPI, benchmark) non mostrano il simbolo %, ma la colonna Unità dice Percentuale. Il PDF del pacchetto è verificato solo dal test automatico, playwright-cli non apre il PDF.
