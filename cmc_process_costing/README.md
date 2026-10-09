# CMC - Process Costing (cmc_process_costing)

Cost of production report per department (CMC cost center) and calendar month (FC-07): quantity schedule,
equivalent units per cost element (transferred-in, materials, conversion), cost per equivalent unit
(weighted average or FIFO), normal / abnormal spoilage, ending WIP, control total, chaining of departments.

## Use
1. Menu Operations > Process Costing > Process Runs: new run (department, process output, month, method).
2. Type the WIP percentages and the ending units, then **Prefill from MRP** (produced and scrap quantities of the
   `cmc_mrp` feeders; beginning WIP from the previous month run; starts from the upstream department run).
3. **Compute**, then print the Cost of Production Report. Reset to draft to change the inputs.

## Rules
- Conversion = MOD + overhead + the `cost_received` of the closing run (overhead from service centers).
- A chained department excludes the materials of the upstream product (that consumption is the transferred-in cost).
- Spoilage is detected at the end of the process (100 % of every element); normal spoilage is a % of the good
  units and is absorbed by the transferred-out cost; the excess is an abnormal loss of the period.
- The usage of the closing, previous month and upstream runs is recorded (AD-002): they cannot be reset meanwhile.

## Deviation from AD-112
Costs of the run and of its lines are stored **positive** (WIP inventory values, reporting convention as in the job
cost sheet); the analytic sign (cost < 0) is flipped once, when the period cost is read.
