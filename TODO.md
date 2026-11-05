# TODO

- [ ] **Publish a prebuilt container image.** Add a GitHub Actions workflow that builds the image on pushes to
  `main` and on tags and publishes it to `ghcr.io/jmoe24/sparkvault` (amd64 and arm64), then let the compose
  files use `image: ghcr.io/jmoe24/sparkvault:latest` with the build context as an optional override.
  Why: building from a GitHub URL fails on UGOS because the NAS's Docker has no `git` binary
  (`exec: "git": executable file not found in $PATH`), so UGOS users currently have to download the source
  onto the NAS first. A prebuilt image removes that step and the build time. Remember to set the package
  visibility to public after the first publish.
- [ ] Reboot test on the NAS (durability check): confirm the project comes back after a NAS restart.
