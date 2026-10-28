### Added

- Cadence analysis (timestamps, VFR, dropped-frame gaps, hard telecine, repeated pictures); repeated pictures that hold in several places lower the frame rate used for bits-per-pixel (#9).
- Click/crackle detection and a bandwidth estimate for audio; lossless audio that stops at or below 16 kHz is flagged (#16).
- Frozen-video detection and per-audio-track decode errors and timestamp discontinuities in the full integrity check (#12, #13).
- MVC detection from the dependent-view stream profile (#15).
