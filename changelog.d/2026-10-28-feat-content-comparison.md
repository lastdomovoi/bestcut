### Added

- `sequence.py`: synchronized comparison of two videos (cached per-second thumbnails, global and piecewise timeline alignment, SSIM/PSNR checkpoints, stream payload hashes). Classifies byte-identical, payload-identical, decoded-equivalent, same-cut re-encode, different cut and different content (#8).
- Colour/contrast grade and colour-metadata checks (#10), inserted/replaced/glitch/overlay detection with an overlay penalty (#11), redundant-larger-re-encode verdicts that keep the compact copy (#14).
- `--content` prints the comparison report; `--recommend-delete` now requires proven identical video, refuses deletes that would lose audio/subtitle tracks, and lists everything unproven for review.
