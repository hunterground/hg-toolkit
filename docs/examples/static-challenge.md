# Author a static challenge

This walkthrough builds `forensic-note-01`, a handout challenge. Players download one file and submit the flag written in that file. There is no container, no base image, and no generated flag. `spec.flags[].content` is the string the platform accepts.

Python 3.11 or newer. This challenge does not need Docker.

## Scaffold

Scaffold the static challenge by ommitting `--base` or set `--base` to `none`.

```sh
hg challenge scaffold --id forensic-note-01 --title "Locker note" --author xathrya \
  --category forensic --dest ../forensic-note-01
cd ../forensic-note-01
```

`--category` is required because a static challenge has no base to take it from. Scaffold prints the created path and writes:

```text
forensic-note-01/
  challenge.yaml
  .gitignore
  private/build.sh
  private/solve.sh
  private/writeup.md
```

`challenge.yaml` starts with `runtime: static` and flag content `hg{replace-me}`. `private/build.sh` writes `dist/handout.txt`, which stays out of git. There is no Dockerfile and no digest.

Replace the description, the flag, the handout, the solver, and the writeup. The finished tree:

```text
forensic-note-01/
  challenge.yaml
  .gitignore
  private/build.sh
  private/solve.sh
  private/writeup.md
  dist/handout.txt    # produced by build, omitted from git
```

### Manifest

Replace the scaffold description, difficulty, value, and flag content. `runtime: static` omits `spawn`, `services`, `hosts`, `networks`, and `player`. The flag type is `static` and `content` is required. One flag is what the platform stores.

`spec.files` is the player handout. `source: build` means `private/build.sh` writes the file under `dist/`. `dist/` stays out of git.

```yaml
apiVersion: hg.challenge/v1
kind: Challenge
metadata:
  id: forensic-note-01
  title: Locker note
  category: forensic
  description: The note in the handout has the locker code.
  difficulty: 1
  author: xathrya
  value: 50
  state: hidden
  version: "1"
spec:
  runtime: static
  flags:
    - id: flag
      type: static
      content: hg{static-note}
  scripts:
    test: private/solve.sh
    build: private/build.sh
  files:
    - name: handout.txt
      source: build
```

`category` is one of `web`, `reversing`, `pwn`, `forensic`, `crypto`, `osint`, `mobile`, `blockchain`.

### Build script

`hg challenge build` runs `scripts.build` with `sh`. In this example, The script must leave `dist/handout.txt` in place. Replace the scaffold `private/build.sh`, which writes the placeholder flag:

```sh
#!/bin/sh
set -eu
mkdir -p dist
printf '%s\n' 'The locker code is hg{static-note}.' > dist/handout.txt
```

### Solver

`verify` runs `private/solve.sh` from the challenge root and checks that stdout contains `content`. It does not set `CHALLENGE_HOST`, `CHALLENGE_PORT`, `CHALLENGE_CONNECTION`, or `CHALLENGE_FLAG_FLAG`. Those variables belong to a running container. Replace the scaffold solver, which exits 1:

```sh
#!/bin/sh
set -eu
grep -o 'hg{[^}]*}' dist/handout.txt
```

### Writeup

`lint --strict` rejects the scaffold writeup stub. Replace `private/writeup.md`:

```markdown
# Writeup

The handout is `handout.txt`. The locker code is the `hg{...}` string in that file.
```

### Gitignore

Scaffold already wrote `.gitignore`. `source: build` requires the `dist/` line:

```gitignore
dist/
.hg/
```

## Check it locally

Run these from the challenge root (or `forensic-note-01`). Alternatively, run another directory by adding `--path /path/to/forensic-note-01`.

```sh
hg challenge lint
```

Prints `lint passed`. A static tree that sets `spawn`, `services`, `hosts`, or `compose` fails. A flag without `content`, or a `dynamic` flag, fails.

```sh
hg challenge lint --strict
```

Prints `lint passed`. Strict lint still requires a real writeup and solver. It does not require `build.fromDigest`, because there is no image.

```sh
hg challenge build
```

Runs `private/build.sh` and exits 0. `dist/handout.txt` is the handout. The command prints no image tag.

```sh
sh private/solve.sh
```

Prints `hg{static-note}`.

```sh
hg challenge verify
```

Runs strict lint, the build script, and the solver. Prints `verified`. There is no container to stop. `hg challenge status` prints `static`.

`hg challenge solve` and `hg challenge run` are for a container or scenario. `solve` stops with: a static challenge has no runtime graph to solve against; run the script directly or use `hg challenge verify`.

## Publish

Build first so `dist/handout.txt` exists. Staff commands talk to `https://hntr-backend.archonlabs.id`. Set `HG_API_URL` for another host. The password is prompted and is not stored.

```sh
hg login --email staff@example.com
hg challenge push
```

`push` creates or updates the catalogue record and uploads `dist/handout.txt`. The record has `requires_instance: false` and `flag_mode: static`. The flag sent is `hg{static-note}`. A new challenge stays a draft. `--publish` sets the status to published. Pass no `--image`.

`hg challenge material` uploads the handout again after the record exists.
