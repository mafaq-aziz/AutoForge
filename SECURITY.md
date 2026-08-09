# Security

AutoForge is a simulation of an automobile company. Its threat model is small,
but the project still follows basic security hygiene.

## What this project is not

AutoForge is not software for real vehicle, factory, robot, or other
safety-critical control. ADAS, battery/BMS, robotics, and ML modules are
experimental simulation models. Do not use, deploy, or represent them as
safety-critical production software.

## Security posture

- **No secrets in the repository.** Never commit API keys, tokens, passwords,
  or private keys. Runtime secrets belong in `.env` (see `.env.example`),
  which is gitignored.
- **Validate external input.** Any data entering the simulator from files,
  configuration, or (later) APIs is validated at the boundary by Pydantic
  models before it reaches simulation logic.
- **Dependencies.** The dependency set is deliberately small and reviewed.
  Before adding a dependency, ask whether the functionality justifies it.
- **No unsafe integrations.** The simulator performs no network calls during a
  run. Future integrations (web dashboard, ML serving) must be reviewed before
  they are added.
- **Reproducibility over trust.** Every run records its seed, configuration,
  and version, so any result can be re-derived and audited.

## Reporting a vulnerability

For now this project has no public disclosure channel. If you find a
vulnerability, open an issue describing the impact and a minimal reproducer,
and refrain from exploiting it beyond the proof of concept.
