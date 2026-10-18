# Author a challenge

This walkthrough builds `web-note-01`, a small web challenge on the `web-flask` base. The service listens on HTTP 5000. `run` writes a dynamic flag to `/app/flag.txt`. The app reads that file from a route a player can request. A handout with a fixed flag is [static-challenge.md](static-challenge.md).

Scaffold writes the tree outside this repository. The destination must be missing or empty. Run the commands below from a checkout of the toolkit so `../web-note-01` lands beside it.

Python 3.11 or newer. Scaffold reads the base digest through Docker. `build` and `run` need Docker.

## Install and pull the base

```sh
pip install -e .
hg bases pull web-flask
```

`pip install -e .` puts `hg` on `PATH`. `bases pull` fetches `hunterground/base-web-flask:latest` and retags it to `base/web-flask:latest`, the name the Dockerfile uses.

## Scaffold

```sh
hg challenge scaffold --base web-flask --id web-note-01 --title "Staff note" --author xathrya --dest ../web-note-01
cd ../web-note-01
```

`--base` accepts `web-flask` or `base/web-flask`. Scaffold prints the created path and writes:

```text
web-note-01/
  challenge.yaml
  .gitignore
  private/writeup.md
  private/solve.sh
  services/web/Dockerfile
  services/web/app.py
```

`challenge.yaml` starts with `spawn: image` and `state: hidden`. The Dockerfile is:

```dockerfile
ARG BASE_WEB_FLASK_IMAGE=base/web-flask:latest
FROM ${BASE_WEB_FLASK_IMAGE}

COPY app.py /app/app.py
```

`private/writeup.md` and `private/solve.sh` are stubs. `lint --strict` and `verify` reject those stubs, so replace both before the strict check.

Scaffold also writes `build.fromDigest`. That value is the manifest-list digest of `base/web-flask:latest` on Docker Hub, which is the pin `lint --strict` and `verify` require. Leave it. If you later change the Dockerfile's base tag, replace the digest with the digest of that tag.

## Edit the challenge

### Description

Replace the scaffold description, and set difficulty and value. Leave `state: hidden`. The finished manifest:

```yaml
apiVersion: hg.challenge/v1
kind: Challenge
metadata:
  id: web-note-01
  title: Staff note
  category: web
  description: The staff note page returns more than it should.
  difficulty: 2
  author: xathrya
  value: 100
  state: hidden
  version: "1"
spec:
  runtime: container
  spawn: image
  platform: [linux/amd64, linux/arm64]
  flags:
    - id: flag
      type: dynamic
      path: /app/flag.txt
      owner: ctf:ctf
      mode: "0400"
      service: web
  scripts:
    test: private/solve.sh
  services:
    - name: web
      build:
        context: services/web
        dockerfile: Dockerfile
        fromDigest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      image:
        name: challenges/web/web-note-01
      ports:
        - name: http
          container: 5000
          protocol: tcp
          publish: true
      health:
        type: http
        port: 5000
        path: /
      resources:
        memoryMb: 128
        cpuMhz: 200
  networks:
    - name: default
      internal: true
      members: [web]
  player:
    entry: web.http
    connection: "{url}"
```

The `fromDigest` value above is a placeholder shape (`sha256:` plus 64 hex characters). Scaffold writes the real digest.

`run` injects the flag at `path` before the service starts. `player.entry` is `web.http`, so the player URL is the published port of the `web` service.

### App

`services/web/app.py` keeps a healthy index page and leaks the flag on `/note`:

```python
from flask import Flask

app = Flask(__name__)


@app.get("/")
def index():
    return "staff notes are internal\n"


@app.get("/note")
def note():
    with open("/app/flag.txt", encoding="utf-8") as handle:
        return handle.read()
```

Health checks `GET /`. The flag route is the bug a player finds.

### Solver

`solve` and `verify` run `private/solve.sh`. The script must print the flag on stdout. A non-zero exit, or output that does not contain the flag, fails the command.

These variables are set for the script:

| Variable | Meaning |
|----------|---------|
| `CHALLENGE_HOST` | `127.0.0.1` |
| `CHALLENGE_PORT` | Published host port |
| `CHALLENGE_CONNECTION` | Player URL, from `player.connection` |
| `CHALLENGE_FLAG_FLAG` | Dynamic flag for this run (`FLAG` is the flag id, uppercased) |

Read the flag the way a player would. `CHALLENGE_FLAG_FLAG` is for a solver that must know the generated value. This one requests the leak:

```sh
#!/bin/sh
# CHALLENGE_HOST, CHALLENGE_PORT, CHALLENGE_CONNECTION
# CHALLENGE_FLAG_FLAG                  dynamic flag for this run
set -eu
curl -fsS "$CHALLENGE_CONNECTION/note"
```

Keep the file executable (`chmod +x private/solve.sh`). Scaffold already sets that bit.

### Writeup

Replace the stub so strict lint accepts the tree:

```markdown
# Writeup

`GET /note` reads `/app/flag.txt` and returns the body. Request that path on the player URL.
```

## Check it locally

Run these from `../web-note-01`. From another directory, add `--path ../web-note-01`.

```sh
hg challenge lint
```

Prints `lint passed`. Unknown keys in `challenge.yaml` fail here.

```sh
hg challenge lint --strict
```

Prints `lint passed` once the writeup and solver are no longer the scaffold stubs. Removing `build.fromDigest` reports `web build.fromDigest is required`.

```sh
hg challenge build
```

Prints `built challenges/web/web-note-01:local`. A missing base is pulled from Docker Hub under the `hunterground` org and retagged.

```sh
hg challenge run
```

Binds the published port on `127.0.0.1`, writes a flag shaped `hg{` plus 32 hex characters, and prints one row each for:

- `connection`, the player URL `http://127.0.0.1:<port>`
- `flag`, the flag id `flag` and the generated value
- `container`, `hg-web-note-01-web` and `challenges/web/web-note-01:local`

The host port is chosen by Docker. `run` returns after the health check. The container keeps running.

```sh
hg challenge status
```

Prints `running` and `web-note-01` while that container is up, then the connection, the flag, and one container row with its state, how long it has been up, and the published port. `hg-web-note-01-default` is `present` when the network still exists. The health row is `ok` when the player entry answers. From another directory, pass `--path ../web-note-01`. The command exits non-zero when the container is not running.

```sh
hg challenge solve
```

Prints the flag from `GET /note`, then a `# target http://127.0.0.1:<port>` line on stderr. Run `solve` as often as you need against the same `run`.

```sh
hg challenge stop
```

Prints `stopped` and the container name. The `:local` image stays. `hg challenge clean` removes that tag as well.

```sh
hg challenge verify
```

Runs the CI check: strict lint, build, run, solver, then cleanup. Prints `verified`.

`hg challenge digest` prints the canonical `sha256:` of `challenge.yaml`. That digest is the manifest, separate from `build.fromDigest`.

## Publish

`hg challenge build` leaves `challenges/web/web-note-01:local`. Log in to Docker Hub with the account that owns the public repository, then publish that image. Staff commands talk to `https://hntr-backend.archonlabs.id`. Set `HG_API_URL` for another host. The password is prompted and is not stored.

```sh
docker login
hg images push --namespace <dockerhub-user>
hg login --email staff@example.com
hg challenge push
```

`hg images push` prints:

```text
web  <dockerhub-user>/challenges-web-web-note-01@sha256:…
```

It writes the pin under the `web` service. `image.name` stays `challenges/web/web-note-01`:

```yaml
      image:
        name: challenges/web/web-note-01
        registry: <dockerhub-user>/challenges-web-web-note-01
        digest: sha256:…
```

The Hub repository must be public. `hg challenge build` tags the host architecture, so push that image only when the platform nodes run the same one.

`hg challenge push` creates or updates the catalogue record and sends `image.registry@image.digest`. It does not upload the image again. A new challenge stays a draft. `--publish` sets the status to published. `private/writeup.md` is sent once it is no longer the scaffold stub.
