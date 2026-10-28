### Fixed

- Full integrity check decodes video and all audio, attributes errors to a stream with an error span, reports timestamp irregularities separately; damaged files never win and are never offered for deletion without a verified-clean copy; `--recommend-delete` forces the full check and lists damaged files for recovery (#12).
- Black bars no longer earn resolution or BPP: active picture measured on six scenes with outlier-robust consensus (#7).
- Audio channel analysis samples several scenes and uses inter-channel correlation (identical, phase-inverted, matrixed, near-duplicate); stereo with equal L/R levels is no longer called mono (#13).
