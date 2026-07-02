# One-time setup: publish the Docker image

Move `docker-publish.yml` into `.github/workflows/` (GitHub web UI:
Add file -> Create new file -> `.github/workflows/docker-publish.yml`,
paste the contents, commit). From then on every push to master builds
and publishes `ghcr.io/bmcgarybot/slugtube:latest` (amd64 + arm64).

After the first run: repo Settings -> Packages -> set the package to
Public so anyone can pull it.
