# TODO

- [ ] **Publish a prebuilt container image.** Add a GitHub Actions workflow that builds the image on pushes to
  `main` and on tags and publishes it to `ghcr.io/jmoe24/sparkvault` (amd64 and arm64), then let the compose
  files use `image: ghcr.io/jmoe24/sparkvault:latest` with the build context as an optional override.
  Why: building from a GitHub URL fails on UGOS because the NAS's Docker has no `git` binary
  (`exec: "git": executable file not found in $PATH`), so the deploy route is "copy the repo folder to the server
  and build there" (`scripts/sync-app.sh`). A prebuilt image would remove the copy and the build time, and the
  server would only need the compose file and `sparkvault.env`. Remember to set the package visibility to public
  after the first publish.
- [ ] Reboot test on the NAS (durability check): confirm the project comes back after a NAS restart.
