# Author a contest

This walkthrough builds `spring-2026`, an `open` weekend contest that contains the challenge from [challenge.md](challenge.md).

A contest is one `contest.yaml` plus one directory per challenge. Every immediate child directory that holds a `challenge.yaml` is included. Scaffold writes the tree outside this repository. The destination must be missing or empty.

`open` and `mini-open` are individual. The other types accept `participation: individual` or `team` and default to `individual`. This example leaves participation off the manifest, so the platform record stays individual.

## Scaffold the contest

Run this from a checkout of the toolkit:

```sh
hg contest scaffold --id spring-2026 --title "Spring 2026" --type open \
  --starts-at 2026-04-01T00:00:00Z --ends-at 2026-04-03T00:00:00Z \
  --dest /path/to/spring-2026
```

`--starts-at` and `--ends-at` are RFC 3339. The end must be after the start. Scaffold prints the created path and writes:

```text
spring-2026/
  contest.yaml
  README.md
  .gitignore
```

`contest.yaml` starts with `visibility: draft` and `description: Replace this description.` `spec.challenges` is omitted until you add it. A draft contest is not published on deploy.

## Add the challenge

Scaffold the challenge as a child. The child directory must be missing or empty:

```sh
hg challenge scaffold --base web-flask --id web-note-01 --title "Staff note" --author xathrya \
  --dest /path/to/spring-2026/web-note-01
```

If you already built `../web-note-01` by following [challenge.md](challenge.md), move that tree instead:

```sh
mv ../web-note-01 /path/to/spring-2026/web-note-01
```

Finish the challenge the same way as that walkthrough: set the description and difficulty, replace `services/web/app.py`, `private/solve.sh`, and `private/writeup.md`, then `hg challenge verify --path /path/to/spring-2026/web-note-01`. Scaffold already wrote `build.fromDigest`.

The directory name is what `spec.challenges[].path` refers to. Here it matches `metadata.id`.

A static handout is another child. Scaffold it without a base. `--base` defaults to `none`, so the tree has no image and no digest:

```sh
hg challenge scaffold --id forensic-note-01 --title "Locker note" --author xathrya \
  --category forensic --dest /path/to/spring-2026/forensic-note-01
```

[static-challenge.md](static-challenge.md) finishes that handout. Add a second `spec.challenges` entry for it.

## Edit contest.yaml

Replace the description and list the challenge. `points` overrides `metadata.value` (100 on the scaffold). `sortOrder` is the scoreboard order. `state` is a local preview (`visible` or `hidden`, default `hidden`) and is not sent as the platform visibility of the challenge.

A child with `challenge.yaml` is still included when `spec.challenges` is omitted. The list sets order, points, and that local preview. A `path` with no `challenge.yaml` fails lint.

```yaml
apiVersion: hg.contest/v1
kind: Contest
metadata:
  id: spring-2026
  title: Spring 2026
  description: Beginner friendly weekend CTF.
  type: open
spec:
  startsAt: 2026-04-01T00:00:00Z
  endsAt: 2026-04-03T00:00:00Z
  visibility: draft
  challenges:
    - path: web-note-01
      points: 250
      sortOrder: 1
```

Leave `visibility: draft` until you intend to publish. Set it to `published` when `deploy` should publish the contest.

The tree is then:

```text
spring-2026/
  contest.yaml
  README.md
  .gitignore
  web-note-01/
    challenge.yaml
    private/
    services/web/
```

## Check it locally

```sh
hg contest lint --path /path/to/spring-2026
```

Prints `lint passed`. This checks `contest.yaml` and lints every child challenge. With no challenge directory, lint reports `no challenge directories found; add one with a challenge.yaml`.

```sh
hg contest lint --path /path/to/spring-2026 --strict
```

Prints `lint passed` when every child also passes strict lint (the digest scaffold wrote, a real writeup, a real solver).

```sh
hg contest challenges --path /path/to/spring-2026
```

Prints one row per challenge, listed challenges first:

```text
ORDER  CHALLENGE    POINTS  STATE   PATH
1      web-note-01  250     hidden  web-note-01
```

`POINTS` is the override from `spec.challenges`. `STATE` is the local preview.

```sh
hg contest digest --path /path/to/spring-2026
```

Prints the canonical `sha256:` of `contest.yaml`.

From inside `/path/to/spring-2026`, the same commands work without `--path`.

## Deploy

Log in once. The password is prompted and is not stored. Tokens go into the OS keychain.

Publish each instance image before deploy. `hg challenge build` must already have produced the `:local` tag. The Docker Hub repository must be public.

```sh
docker login
hg images push --namespace <dockerhub-user> --path /path/to/spring-2026/web-note-01
hg login --email staff@example.com
hg contest deploy --path /path/to/spring-2026
```

`hg images push` writes `image.registry` and `image.digest` on the player service. `deploy` creates or updates the contest, pushes each challenge, and uploads handouts built into `dist/`. It does not `docker push`. It sends `image.registry@image.digest`. With `visibility: draft` the contest stays unpublished. Set `visibility: published` before deploy when the contest should go live.

## Challenges already on the platform

When the challenges are already in the catalogue, create the contest on the platform and link them by slug:

```sh
hg contest create --type open --slug spring-2026 --title "Spring 2026" \
  --starts-at 2026-04-01T00:00:00Z --ends-at 2026-04-03T00:00:00Z
hg contest add-challenge spring-2026 web-note-01 --points 250
hg contest publish spring-2026
```

`create` does not read a `contest.yaml`. `add-challenge` links a catalogue slug. `publish` marks that contest published.
