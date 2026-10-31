# Limitations

- **Small and single-domain.** 120 synthetic cases for one fictional refund policy. This
  is a regression suite, not a general benchmark or leaderboard.
- **Templated language.** Real customers phrase requests far more variably; the reference
  policy's phrase rules would not survive real traffic, by design.
- **Structured correctness only for the gate.** The `message` text is checked only by a
  small fabricated-action phrase canary and the advisory judge.
- **Same-family judge.** The judge (Qwen3-8B) and actor (Qwen3-4B) share a model family, so
  their mistakes may be correlated. Judge agreement is pending human labels.
- **Read-only tools.** Since no tool can move money, the platform demonstrates
  policy-attempt detection and disclosure detection, not prevention of real financial loss.
- **Simulated identity.** Ownership is bound by the harness, not by real authentication.
- **Reproducibility bounds.** Temperature 0 and fixed seeds reduce variance; results can
  still differ across hardware, Ollama versions and quantisations. Manifests record all
  three so differences are attributable.
- **Public held-out split.** It disciplines development; it cannot rule out similar text in
  pretraining data.
- **Few held-out families.** 12 families make any interval on the held-out split exploratory.
- **Controlled mutations are a defined set.** "8/8 detected" covers those eight injected
  bugs, not all possible agent failures.
- **Local single-user tool.** The dashboard binds to localhost with no authentication.
