# HunterGround Toolkit

Command-line toolkit for authoring [HunterGround](https://hunterground.io) challenges and contests. It scaffolds a tree from a base image, lints `challenge.yaml`, builds and runs the challenge locally, checks a solver, and pushes the record to the platform.

Python 3.11 or newer. An image scaffold reads the base digest through Docker. `build` and `run` need Docker for a container or scenario.

```sh
pip install -e .
```

That puts `hg` on `PATH`.

## Author a challenge

A challenge is one `challenge.yaml` plus its build context. Scaffold writes the tree outside this repository. The destination must be missing or empty.

```sh
hg bases pull web-nginx
hg challenge scaffold --base web-nginx --id web-xss-01 --dest ../web-xss-01
cd ../web-xss-01
```

Scaffold writes `challenge.yaml` (`spawn: image`, `state: hidden`), a Dockerfile that `FROM`s the base through a build-arg, `private/writeup.md`, and `private/solve.sh`. It fills `build.fromDigest` with the base image's manifest-list digest. `--base` accepts `web-nginx` or `base/web-nginx`. Omitting `--base` writes a static challenge with no instance and needs `--category`.

Edit the app, replace the writeup and solver stubs, then check it locally. [docs/examples/challenge.md](docs/examples/challenge.md) walks through a Flask challenge from scaffold to `verify`.

```sh
hg challenge lint
hg challenge build
hg challenge run
hg challenge status
hg challenge solve
hg challenge stop
```

`lint` checks the tree against the schema. Unknown keys fail. `build` tags `challenges/<category>/<id>:local` (for example `challenges/web/web-xss-01:local`) and pulls a missing base from Docker Hub under the `hunterground` org. `run` binds published ports on `127.0.0.1`, generates each dynamic flag as `hg{` plus 32 hex characters, and prints the player connection. `status` reports whether each container is running, the published port, and the connection recorded by `run`. It exits non-zero when the graph is not fully up. A static challenge reports `static`. `solve` runs `scripts.test` against that graph. `stop` removes the containers and keeps the image. `clean` removes the `:local` tag as well.

`verify` is the check CI runs: strict lint, build, run, solver, then cleanup.

```sh
hg challenge verify
```

`--strict` requires the `build.fromDigest` scaffold wrote, a writeup and solver that are not the scaffold stubs, and a `metadata.id` that matches `main` once `main` has one. `hg challenge digest` prints the canonical `sha256:` of the manifest.

### Manifest

The schema is [`src/hg/schema/challenge-v1.json`](src/hg/schema/challenge-v1.json). A web challenge looks like this:

```yaml
apiVersion: hg.challenge/v1
kind: Challenge
metadata:
  id: web-xss-01            # 3..64 chars: letter, then words of [a-z0-9] separated by -
  title: Reflected note
  category: web             # web, reversing, pwn, forensic, crypto, osint, mobile, blockchain
  description: Can you get the admin bot to load your page?
  difficulty: 3             # 1..10, or easy | medium | hard
  author: xathrya
  value: 100
  state: hidden             # hidden | visible
  version: "1"
spec:
  runtime: container        # container | scenario | static
  spawn: image              # image | compose. Omitted for static
  platform: [linux/amd64, linux/arm64]
  flags:
    - id: flag
      type: dynamic         # dynamic | static | regex
      path: /app/flag.txt
      owner: nginx:nginx
      mode: "0400"
      service: web
  scripts:
    test: private/solve.sh
  services:
    - name: web
      build:
        context: services/web
        dockerfile: Dockerfile
        fromDigest: sha256:…   # pinned by scaffold; required by lint --strict
      image:
        name: challenges/web/web-xss-01
      ports:
        - name: http
          container: 80
          protocol: tcp
          publish: true
      health:
        type: http          # tcp | http | exec | ready-file
        port: 80
        path: /
        status: 200
      resources:
        memoryMb: 128
        cpuMhz: 200
  networks:
    - name: default
      internal: true
      members: [web]
  player:
    entry: web.http         # <member>.<port-name>
    connection: "{url}"     # {url} {host} {port} {user}
```

`container` is one image or a small set. `scenario` is multi-daemon image (the scaffold base is `range/debian`). `static` has no Docker graph: players download `spec.files` and submit a `static` or `regex` flag. [docs/examples/static-challenge.md](docs/examples/static-challenge.md) walks through a handout whose flag is the string in the manifest.

`spawn: image` keeps `services` and `networks` in the manifest. `spawn: compose` keeps the graph in `compose.yaml` and omits those keys. The player port is a label on the service, `hg.port.http: "5000"`, and `player.entry` is still `web.http`.

The Dockerfile scaffold writes for `web-nginx`:

```dockerfile
ARG BASE_WEB_NGINX_IMAGE=base/web-nginx:latest
FROM ${BASE_WEB_NGINX_IMAGE}

COPY html/ /app/html/
```

### Solver

`solve` and `verify` export the same environment. The script must print the flag on stdout. A non-zero exit, or output missing a required flag, fails the command.

```sh
#!/bin/sh
# CHALLENGE_HOST, CHALLENGE_PORT, CHALLENGE_CONNECTION
# CHALLENGE_FLAG_<ID>                  dynamic flag for this run
# CHALLENGE_USER_<MEMBER>_<NAME>       generated password
set -eu
curl -fsS "$CHALLENGE_CONNECTION"
```

Read the flag from the service the way a player would. Use `CHALLENGE_FLAG_FLAG` only when the solver needs the generated value. `hg challenge solve --script private/probe.sh` runs another script. `run` once, then `solve` as often as you need.

## Author a contest

A contest is one `contest.yaml` plus one directory per challenge. Every child directory that holds a `challenge.yaml` is included. `spec.challenges` is optional and only sets order, points, and a local visibility preview. [docs/examples/contest.md](docs/examples/contest.md) walks through an `open` contest that contains one challenge.

```sh
hg contest scaffold --id spring-2026 --title "Spring 2026" --type open \
  --starts-at 2026-04-01T00:00:00Z --ends-at 2026-04-03T00:00:00Z \
  --dest ../spring-2026

hg contest lint --path ../spring-2026
hg contest challenges --path ../spring-2026
```

```yaml
apiVersion: hg.contest/v1
kind: Contest
metadata:
  id: spring-2026
  title: Spring 2026
  description: Beginner friendly weekend CTF.
  type: open          # open | mini-open | public-competition | private-competition | hiring-test
spec:
  startsAt: 2026-04-01T00:00:00Z
  endsAt: 2026-04-03T00:00:00Z
  visibility: draft   # draft | published
  challenges:
    - path: web-xss-01
      points: 250     # default: metadata.value
      sortOrder: 1
```

`open` and `mini-open` are individual. The other types accept `participation: individual` or `team` and default to `individual`.

## Platform

Staff commands talk to `https://hntr-backend.archonlabs.id`. Set `HG_API_URL` for another host. The password is prompted and is not stored. Tokens go into the OS keychain.

```sh
docker login
hg images push --namespace <dockerhub-user>
hg login --email staff@example.com
hg whoami

hg challenge push
hg contest deploy --path ../spring-2026
```

`hg images push` tags the `:local` image as `<dockerhub-user>/challenges-<category>-<id>:<version>`, pushes it with the current Docker login, and writes `image.registry` and `image.digest`. The Hub repository must be public. `hg challenge build` tags the host architecture, so publish that image only when the platform nodes run the same one.

`hg challenge push` creates or updates the catalogue record. It does not upload the image again. It sends `image.registry@image.digest`, one flag, the port from `player.entry`, and `private/writeup.md` once that file is no longer the scaffold stub. A new challenge stays a draft unless you pass `--publish`. `deploy` creates or updates the contest, pushes each challenge, uploads handouts built into `dist/`, and publishes the contest when `visibility: published`. It reads the pin `hg images push` wrote.

A contest built from challenges already on the platform:

```sh
hg contest create --type private-competition --slug acme-2026 --title "Acme 2026" \
  --starts-at 2026-04-01T00:00:00Z --ends-at 2026-04-03T00:00:00Z
hg contest add-challenge acme-2026 web-xss-01 --points 250
hg contest publish acme-2026
```

## Images

`hg images list` prints each built member, its `:local` tag, and `registry@digest` once `hg images push` has recorded both. `hg images push --namespace <dockerhub-user>` publishes those images. `HG_REGISTRY` supplies the namespace when `--namespace` is omitted. Docker credentials stay in the Docker config; this command does not log in.

## Bases

`hg bases list` prints every base. Scaffold accepts the ones that have a template:

| Base | Listen |
|------|--------|
| `web-nginx`, `web-php-nginx`, `web-php-apache` | HTTP 80 |
| `web-flask` | HTTP 5000 |
| `web-fastapi`, `web-express` | HTTP 8000 |
| `web-sinatra` | HTTP 4567 |
| `java`, `python`, `nodejs`, `ruby`, `php` | HTTP 8080 |
| `python-socat`, `nodejs-socat` | TCP 5000, `nc {host} {port}` |
| `range/debian` | SSH 22 |

`hg bases pull` with no name pulls every base and retags it to the bare name the templates use (`base/web-nginx:latest`). A name, or several names, pulls only those bases. A missing image is reported and the rest of the set is still pulled. The command exits non-zero after the set finishes when any image was missing.

## Builders

`hg builders list` prints every compile-stage image. `hg builders pull` with no name pulls every builder and retags it to the bare name a challenge Dockerfile uses (`builder/cpp:latest` from `hunterground/builder-cpp:latest`). A name, or several names, pulls only those builders (`hg builders pull cpp java`). A missing image is reported and the rest of the set is still pulled.

`anchor` is `linux/amd64` only. The other builders are `linux/amd64` and `linux/arm64`.

`hg challenge build` pulls a missing catalog builder stage the same way it pulls a missing base. `FROM scratch` and upstream images are left as written.

## Completion

```sh
eval "$(hg completion bash)"   # ~/.bashrc
eval "$(hg completion zsh)"    # ~/.zshrc, after compinit
hg completion fish > ~/.config/fish/completions/hg.fish
```

## Tests

```sh
pip install -e '.[dev]'
pytest
```

The tests do not need a Docker daemon.
