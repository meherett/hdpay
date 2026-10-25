---
name: release
description: Build and push HDPay's Docker images to ghcr.io with a version tag. Use whenever an image is built or pushed (api, watcher, sweeper, web, example-api, example-web), or when asked to release, tag or version them.
---

# Releasing HDPay's Docker images

Every image that is built gets a version tag as well as `latest`. Never push only `latest`.

## Choosing the version

The current version of each image is in `versions.json`, next to this file. Read it, decide the
bump from what changed in that image since its last version, and write the new version back.
An image at `null` was never versioned: its first version is 0.1.0.

| Bump | When | Example |
|---|---|---|
| patch | a small fix or tweak: wording, a renamed file, a bug fix, data or config only | 0.2.0 -> 0.2.1 |
| minor | a notable change: a reworked part, new admin page or API route, a changed behaviour | 0.2.1 -> 0.3.0 |
| major | a big change: a major new feature, a fix for a serious issue, or anything that breaks how it was run or called (database, API, compose, env) | 0.3.0 -> 1.0.0 |

- A minor bump resets the patch to 0 (0.2.1 -> 0.3.0); a major bump resets both (0.3.0 -> 1.0.0).
- Only the images whose code changed get a new version. api, watcher and sweeper share one
  Dockerfile and code: when shared code (`api/`) changes, bump all three together.
- When unsure between two levels, say which you chose and why when reporting.

## Building and pushing

Multi-platform (the VPS and the Macs differ), through the `multi` builder. Start Docker Desktop
first if `docker info` fails.

```
P="--builder multi --platform linux/amd64,linux/arm64"
docker buildx build $P --target api     -t ghcr.io/meherett/hdpay-api:latest     -t ghcr.io/meherett/hdpay-api:<v>     --push .
docker buildx build $P --target watcher -t ghcr.io/meherett/hdpay-watcher:latest -t ghcr.io/meherett/hdpay-watcher:<v> --push .
docker buildx build $P --target sweeper -t ghcr.io/meherett/hdpay-sweeper:latest -t ghcr.io/meherett/hdpay-sweeper:<v> --push .
docker buildx build $P -t ghcr.io/meherett/hdpay-web:latest          -t ghcr.io/meherett/hdpay-web:<v>          --push web
docker buildx build $P -t ghcr.io/meherett/hdpay-example-api:latest  -t ghcr.io/meherett/hdpay-example-api:<v>  --push example/api
docker buildx build $P -t ghcr.io/meherett/hdpay-example-web:latest  -t ghcr.io/meherett/hdpay-example-web:<v>  --push example/web
```

- Builds take minutes: run them in the background, one log per image in the scratchpad.
- Add `--no-cache` when asked for a clean rebuild.
- Afterwards check each tag: `docker buildx imagetools inspect ghcr.io/meherett/<image>:<v>`
  shows its digest and both platforms. Report image, version and digest in a table.
- Never print a token: the git remote URL may carry one (mask `ghp_...` in any output).

## After pushing

- Update `versions.json` and commit it with the code (only when the user asked for a commit).
- Tell the user how to run it on the VPS: in `vm/`, `docker compose pull`, then `docker compose up -d`
  (add `--profile example` for the example shop), and Ctrl+F5 for the web apps.
