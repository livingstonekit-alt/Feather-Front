# Licenses and Distribution Notes

This project depends on third-party software and services.  
Before packaging or redistributing FeatherFront, verify license obligations for each dependency.

## Project license

- FeatherFront repository license: add your chosen license in `LICENSE` (if not already present).

## Third-party components

- BirdNET Analyzer
  - Upstream: https://github.com/kahst/BirdNET-Analyzer
  - Action: confirm code/model redistribution terms and attribution requirements.

- FFmpeg
  - Upstream: https://ffmpeg.org/
  - Action: if bundling binaries, comply with FFmpeg licensing requirements (LGPL/GPL variant based on build).

- Open-Meteo APIs
  - Forecast API: https://open-meteo.com/
  - Geocoding API: https://open-meteo.com/en/docs/geocoding-api
  - Action: verify attribution/usage terms for your deployment scale.

- Google Fonts
  - Bebas Neue: https://fonts.google.com/specimen/Bebas+Neue
  - Space Grotesk: https://fonts.google.com/specimen/Space+Grotesk
  - Action: confirm font license terms if self-hosting font files.

## Packaging guidance

- Include this file and `THIRD_PARTY.md` in release artifacts.
- Keep dependency versions documented for each release.
- Re-check third-party terms before each public release.
