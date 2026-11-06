# NetPulse

A small network toolkit web app: subnet calculator, VLSM planner, DNS lookup and
an SSRF-safe uptime checker. FastAPI backend, vanilla JS front end (no build
step, no CDN), SQLite for history. Packaged as a single Docker image for one EC2
instance.

![NetPulse screenshot](docs/screenshot.png)
<!-- TODO: add a screenshot at docs/screenshot.png -->

## Features

| Tab | What it does |
|-----|--------------|
| Subnet | Network, broadcast, netmask, wildcard, host range and usable hosts for any IPv4/IPv6 CIDR. Handles /31 (RFC 3021) and /32. |
| VLSM | Splits a base IPv4 network into right-sized subnets, largest first, and reports leftover space. |
| DNS | A, AAAA, MX, TXT, NS, CNAME lookups with a 3 s timeout. |
| Uptime | Checks a public http/https URL (5 s timeout, max 3 redirects) and keeps a history in SQLite. |

## Run locally

Requires Python 3.12.

```bash
make install   # creates .venv and installs requirements-dev.txt
make lint      # ruff check .
make test      # pytest with an 80% coverage gate
make run       # http://127.0.0.1:8000 with auto-reload
```

## Run with Docker

```bash
make docker-build            # docker build -t netpulse:local --build-arg APP_VERSION=local .
make docker-run              # http://localhost:8080, data in the netpulse-data volume
curl localhost:8080/health
```

| Environment variable | Default | Purpose |
|----------------------|---------|---------|
| `APP_VERSION` | `dev` | Shown by `/health` and in the UI footer. Set by `--build-arg APP_VERSION=<git sha>`. |
| `DATA_DIR` | `./data` (`/app/data` in the image) | Folder holding `netpulse.db`. Mount a volume here. |

## API

| Method | Path | Body / query | Success | Errors |
|--------|------|--------------|---------|--------|
| GET | `/health` | – | `{"status":"ok","version":"..."}` | – |
| GET | `/api/subnet` | `?cidr=192.168.10.0/26` | network, broadcast, netmask, wildcard, prefix, first_host, last_host, usable_hosts | 422 bad CIDR |
| POST | `/api/vlsm` | `{"base":"10.0.0.0/24","hosts":[50,20,10,2]}` | allocations, leftover, leftover_addresses | 422 invalid / does not fit |
| GET | `/api/dns` | `?domain=example.com&type=A` | domain, type, ttl, records | 422 bad domain/type, 404 NXDOMAIN, 502 resolver error, 504 timeout |
| POST | `/api/check` | `{"url":"https://example.com"}` | url, status_code, response_ms, ok, error | 422 bad URL/scheme, 400 blocked target |
| GET | `/api/checks` | – | last 20 checks, newest first | – |

All `/api/*` routes are limited to 30 requests/minute per client IP (429 with
`Retry-After`). Interactive docs: `/docs`.

A target that is down or times out is a *result* (`ok: false`, HTTP 200), not an
API error; only invalid or blocked URLs are rejected.

## Project layout

```
app/main.py         app factory, middleware (rate limit, security headers), routes
app/subnet.py       subnet maths with the ipaddress module
app/vlsm.py         largest-first VLSM allocator
app/dns_lookup.py   dnspython wrapper + domain validation
app/uptime.py       URL validation, SSRF guard, IP-pinned HTTP check
app/db.py           sqlite3 storage for check history
app/ratelimit.py    in-memory sliding-window limiter
app/templates/      Jinja2 page;  app/static/  JS + CSS
tests/              one test file per module; DNS and HTTP are mocked
.github/workflows/deploy.yml   CI/CD: test -> build/push to ECR -> deploy via SSM
scripts/deploy-ec2.sh          runs on the instance: pull, swap container, health check, roll back
scripts/aws-setup.sh           creates the AWS resources (run once in CloudShell)
scripts/aws-teardown.sh        deletes them all again
```

## Architecture (AWS deployment)

```
GitHub push ──► GitHub Actions ──(OIDC, no stored keys)──► IAM role
                     │ make lint / make test
                     │ docker build --build-arg APP_VERSION=$GITHUB_SHA
                     ▼
                 Amazon ECR  (image tagged with the git SHA)
                     │
                     ▼  aws ssm send-command (no SSH, no open port 22)
                 EC2 instance (SSM agent, instance role with ECR read)
                   docker pull … && docker run -d -p 80:8000 -v netpulse-data:/app/data
```

- **GitHub Actions + OIDC**: the workflow assumes an IAM role through GitHub's OIDC
  provider, so no long-lived AWS keys live in GitHub secrets. The role's trust
  policy is restricted to this repo and branch.
- **ECR**: private registry; each image is tagged with the commit SHA for
  traceable, rollback-friendly deploys. Enable scan-on-push.
- **SSM Run Command**: deploys without SSH. The security group needs only the
  app port inbound (80/443), nothing for management.
- **EC2**: instance profile allows `ecr:GetAuthorizationToken` + pull and SSM.
  Require IMDSv2 (hop limit 1) as defence in depth for the metadata service.
- **Health**: the image `HEALTHCHECK` and the deploy step both use `/health`;
  `version` confirms which commit is live.

## CI/CD setup

`.github/workflows/deploy.yml` has three jobs:

1. **test** (every PR and push): `ruff check .` and pytest with the 80% gate.
2. **build** (push to `main`): assumes the AWS role via OIDC, builds the image with
   `APP_VERSION=<commit sha>`, runs it and checks `/health` reports that sha, then
   pushes `<registry>/<repo>:<sha>` to ECR.
3. **deploy** (after build, `production` environment): sends `scripts/deploy-ec2.sh`
   to the instance with SSM Run Command. The script logs in to ECR with the
   instance role, pulls, replaces the container (`-p 80:8000`, `netpulse-data`
   volume, `--restart unless-stopped`) and waits for `/health` to report the new
   sha. If it doesn't, it **rolls back** to the previous image and the job fails.
   Deploys never run in parallel (`concurrency`).

### One-time AWS setup

**Quick way:** open AWS CloudShell and run

```bash
curl -fsSL https://raw.githubusercontent.com/ZXithunf/networkapp/main/scripts/aws-setup.sh | bash
```

It creates everything below (re-runnable) and prints the four GitHub variables to
add. When you are finished, delete it all so nothing keeps using credits:

```bash
curl -fsSL https://raw.githubusercontent.com/ZXithunf/networkapp/main/scripts/aws-teardown.sh | bash
```

**What the script does, step by step:**

1. **ECR repository**, e.g. `netpulse` (tag immutability on, scan on push).
2. **EC2 instance** (Amazon Linux 2023 has the SSM agent and AWS CLI): install and
   start Docker (`dnf install -y docker && systemctl enable --now docker`); attach an
   instance profile with `AmazonSSMManagedInstanceCore` and
   `AmazonEC2ContainerRegistryReadOnly`; require IMDSv2; security group allows
   inbound 80 only (no SSH needed).
3. **GitHub OIDC provider** in IAM: `token.actions.githubusercontent.com`,
   audience `sts.amazonaws.com`.
4. **IAM role for GitHub** with this trust policy (the build job's subject is the
   branch; the deploy job's subject is the environment):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
         "StringLike": { "token.actions.githubusercontent.com:sub": [
           "repo:ZXithunf/networkapp:ref:refs/heads/main",
           "repo:ZXithunf/networkapp:environment:production"
         ] }
       }
     }]
   }
   ```

   and this permissions policy:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
       { "Effect": "Allow",
         "Action": ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
                    "ecr:BatchGetImage"],
         "Resource": "arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/netpulse" },
       { "Effect": "Allow", "Action": "ssm:SendCommand",
         "Resource": ["arn:aws:ssm:<REGION>::document/AWS-RunShellScript",
                      "arn:aws:ec2:<REGION>:<ACCOUNT_ID>:instance/<INSTANCE_ID>"] },
       { "Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*" }
     ]
   }
   ```

5. **GitHub repo settings**: create the `production` environment (add yourself as
   a required reviewer if you want manual approval), then add Actions
   **variables** (not secrets; none of these are sensitive): `AWS_REGION`,
   `AWS_ROLE_ARN`, `ECR_REPOSITORY`, `EC2_INSTANCE_ID`.

## Security decisions

- **SSRF protection in the uptime checker.** The hostname is resolved by the app
  and *every* returned address must be public: private, loopback, link-local
  (includes `169.254.169.254`, the EC2 metadata service), multicast, reserved,
  unspecified and other non-global ranges (e.g. 100.64.0.0/10) are refused with
  400. IPv4-mapped IPv6 (`::ffff:127.0.0.1`) is unwrapped first.
- **DNS-rebinding defence.** The HTTP request connects to the exact IP that was
  validated, sending the original `Host` header and TLS SNI, so a second DNS
  answer can't swap in an internal address. TLS certificates are still verified
  against the hostname.
- **Redirects followed manually** (max 3) and every hop is re-validated, so a
  public site can't bounce the checker to an internal address.
- **Only http/https**; bodies are never downloaded (status line only), 5 s timeout.
- **Rate limiting** per client IP on `/api/*`. It uses the TCP peer address and
  ignores `X-Forwarded-For`, which clients can forge. It is per process (two
  workers → up to 60/min worst case) and resets on restart; for a hard limit use
  AWS WAF or Redis.
- **Input validation** everywhere with clear 422 messages; size limits on inputs;
  VLSM capped at 64 subnets.
- **SQL injection**: all queries are parameterised.
- **XSS**: the front end builds the DOM with `textContent`, never `innerHTML` with
  API data. The page sends a strict `Content-Security-Policy` (`default-src 'self'`,
  no inline scripts), plus `X-Content-Type-Options`, `X-Frame-Options: DENY` and
  `Referrer-Policy` on every response.
- **Container**: slim base, no build tools, runs as non-root user `app`
  (uid 10001); only `/app/data` is writable. `.dockerignore` keeps tests, `.git`
  and local data out of the image.
- **No long-lived cloud credentials**: GitHub uses OIDC to get short-lived role
  credentials scoped to this repo; the instance uses its instance profile. No SSH
  keys, no open port 22.
- **Tests never touch the network**: a fixture blocks real socket connections and
  DNS/HTTP are mocked.
