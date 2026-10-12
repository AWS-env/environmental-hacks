# run-it

An htop-style live view for the folder you are in: measured power, modelled CO₂e and water, the
process table, scanner findings as they stream in, and a log of everything run-it does.

```bash
cd ~/some-project
run-it                    # watch: scan this folder, live power for your processes
run-it npm start          # run: launch the command, measure its whole process tree
run-it -- python app.py   # use -- when the command has options of its own
```

`q` quits and prints a receipt. `r` rescans, `f`/`p`/`l` focus findings/processes/log, and `enter` on
a finding opens its evidence. In that view, `c` copies the finding's agent prompt.

## Setup (once)

```bash
python3 -m venv .venv
.venv/bin/pip install -r shared/contracts/requirements.txt -r run_it/requirements.txt \
                      -r detectors/owner-c/requirements.txt
npm ci && npm --prefix detectors/owner-a ci && npm --prefix detectors/owner-a run build
ln -s "$PWD/scripts/run-it" ~/.local/bin/run-it   # any directory on your PATH
```

If an owner's toolchain is missing, that owner shows as unavailable and the meters keep running.

## Web dashboard

While run-it runs, it serves its live state at `http://127.0.0.1:47321/state` (JSON, schema `run-it.state/1`).
The site's `/environment` page finds it within a few seconds and shows the meters live: power over time,
energy, CO₂e and water with their sources, where the command's energy went, the process table and the scan.
When run-it is not running, the page shows a recorded session instead.

- Only the hosted site and local dev servers (`localhost:3000`) may read it, because the process table is
  private. Add origins with `RUN_IT_ALLOW_ORIGINS=https://a.example,https://b.example`.
- Requests whose `Host` is not `127.0.0.1`/`localhost` are refused, so DNS rebinding cannot reach it.
- Nothing is written to disk or sent anywhere. The server stops when run-it exits.
- `--port N` picks another port (open `/environment?port=N`), and `--no-serve` turns it off.
- Safari blocks an https page from reading localhost. Use Chrome or Firefox, or run the site locally.

## Where every number comes from

| Number | Source | Tag |
| --- | --- | --- |
| Per-process W and J | `proc_pid_rusage` → `ri_energy_nj`, the kernel's energy counter, sampled every second. No sudo, current user's processes only | measured |
| App W and J (run mode) | Sum over the command's process group, including processes that exit between samples. The direct child is read once more before it is reaped | measured |
| Whole Mac W | SMC telemetry via `ioreg`: `SystemLoad` on the charger, battery V×I on battery. It refreshes slowly, so the panel shows its age | measured, coarse |
| CO₂e | energy × 0.727 kgCO₂e/kWh (CEA CO2 Baseline Database, all-India weighted average, FY2023-24) | modelled |
| Water | energy × ~2.4 L/kWh, generation water only (MoEFCC 2015 thermal-plant cap of 3.0–3.5 m³/MWh × ~75% thermal share). Excludes data-centre cooling | modelled |
| Findings | the repository scanner, streamed per owner as each finishes | static |

Override the factors with `RUN_IT_GRID_KG_PER_KWH` and `RUN_IT_WATER_L_PER_KWH`. The receipt then
labels them as overrides.

## Limitations

- **macOS only for energy.** On other platforms the energy columns show `—`. The processes, findings and log still work.
- **Other users' processes** (root daemons, `WindowServer`) are not readable without sudo, so "your processes" is a partial total. The header shows how many processes were readable.
- **Embodied hardware emissions** (the SCI `M` term) are not included.
- **Findings stay static.** run-it never converts a finding into grams. Measured energy belongs to processes, and findings point at lines.
- **Run mode is non-interactive.** The command gets no stdin. Its stdout and stderr go to the log panel.
