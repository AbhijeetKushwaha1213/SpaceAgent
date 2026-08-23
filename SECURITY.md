# Security Policy & Incident Record — SENTINEL

This document covers (1) how to report a vulnerability, (2) how secrets are
handled in this repository, and (3) a disclosed historical credential-exposure
incident with its required remediation.

> **The credential value is never written in this file.** The exposed key is
> identified only by a non-reversible fingerprint so a human can confirm *which*
> key to revoke without this document itself becoming a secret-bearing artifact.

---

## 1. Reporting a vulnerability

Report suspected vulnerabilities privately to the repository owner (do not open a
public issue for anything that could aid exploitation). Include the affected
component, reproduction steps, and impact. Please allow time for a fix before any
public disclosure.

---

## 2. How secrets are handled

- **No credential is ever committed.** Real values live only in a local,
  git-ignored `.env`. `.gitignore` ignores `.env`, `*.env`, and `.env.*`, and
  re-includes only `*.env.example` templates (`!.env.example`, `!*.env.example`).
- **Templates carry placeholders only.** `sentinel/.env.example` and
  `sentinel/frontend/.env.example` name each variable with a placeholder such as
  `your-gemini-api-key-here` — never a real value.
- **Keys are read from the environment at runtime**, e.g. `GEMINI_API_KEY`
  (`app/llm/provider.py`, `app/agent/agent.py`). Absence produces a clear error;
  no key is inlined in source.
- **No secret reaches the frontend.** The React app is configured only with a
  backend URL (`REACT_APP_BACKEND_URL` / `window.SENTINEL_BACKEND_URL`).
- **A regression guard runs in the test suite.**
  `sentinel/backend/tests/test_secret_scan.py` scans every git-tracked file and
  fails CI if a Google/Gemini-key-shaped string appears outside the documented
  redaction-fixture allowlist, if a real `.env` is tracked, or if a
  `*.env.example` carries a non-placeholder secret value.

---

## 3. Disclosed incident — historical Google/Gemini API key exposure

### 3.1 Summary

A **real Google/Gemini API key** was committed to the environment *template*
`sentinel/.env.example`, then removed from the working tree in a later commit.
The value no longer exists in the current tree, index, or `HEAD`, but it
**remains recoverable from git history** on a branch that is **pushed to a public
GitHub remote**. It must be treated as **compromised** and **rotated**.

### 3.2 Exposed credential (fingerprint only — value withheld)

| Property | Value |
|---|---|
| Kind | Google API key (`AIzaSy…` shape) |
| Length | 39 characters |
| Distinct characters | 28 (confirms a real key, not an all-`A` test dummy) |
| `sha256(value)[:8]` | `df766891` |

Use this fingerprint to identify the exact key to revoke in the Google Cloud
console. If a key you hold hashes to this prefix, it is the exposed one.

### 3.3 Location in history

| Commit | Subject | Effect on `sentinel/.env.example` |
|---|---|---|
| `c57e920` | *feat: implement Sentinel mission control dashboard UI and core communication agent structure* | **introduced** the real key |
| `2e0a491` | *chore: remove placeholder Google API key from environment template* | **removed** the key from the working tree |

Every commit in the range `c57e920..2e0a491` (inclusive of `c57e920`) carries the
key in that blob. Reachability at the time of writing:

- `c57e920` **is an ancestor of `antigravity`** (the active branch).
- `c57e920` **is NOT an ancestor of `main`**.
- `origin` = `https://github.com/AbhijeetKushwaha1213/spaceAgent-_version2.git`
  (public). Because `antigravity` has been pushed there, **the key has been
  publicly exposed** and must be assumed harvested by automated scanners.

### 3.4 Current working-tree status (verified)

- `HEAD:sentinel/.env.example` contains a placeholder only — **no key match**.
- No tracked file in the current tree contains a real-key-shaped string. The only
  `AIzaSy…`-shaped strings that remain are **synthetic fixtures** (all-`A` or
  wrong-length dummies) inside redaction/exfiltration tests, which are allowlisted
  and intentionally present to prove the redaction path masks them.
- No real `.env` file is tracked; only `*.env.example` templates are.

### 3.5 REQUIRED action — rotate the key now (mandatory, not optional)

Rotation is the **only** remediation that actually closes the exposure. History
rewriting (below) reduces future discoverability but **cannot un-expose a value
that has already been public**.

1. In the Google Cloud console, **revoke/delete** the key whose
   `sha256[:8] == df766891`.
2. Issue a **new** key and restrict it (API + referrer/IP restrictions).
3. Place the new key only in a local, git-ignored `.env` (`GEMINI_API_KEY=…`).
4. Review the key's usage/billing logs for anomalous activity during the exposure
   window.

### 3.6 History remediation — DOCUMENTED, NOT PERFORMED

> **This procedure has intentionally NOT been executed by tooling.** `antigravity`
> is shared and published; rewriting its history is a coordinated, destructive
> operation that invalidates every existing clone and open PR. It must be run by a
> human who can coordinate with all collaborators. Rotating the key (§3.5) is what
> actually neutralizes the exposure; the steps below only purge the value from
> future clones **after** rotation.

Recommended approach — [`git-filter-repo`](https://github.com/newren/git-filter-repo)
(preferred over `filter-branch`):

```bash
# 0. Rotate the key first (§3.5). Back up the repo (a full mirror clone).
git clone --mirror https://github.com/AbhijeetKushwaha1213/spaceAgent-_version2.git backup.git

# 1. In a fresh mirror, strip the exposed value from ALL of history.
#    Put ONLY the exposed key value in replacements.txt (one line):
#        <EXPOSED_KEY_VALUE>==>REDACTED-ROTATED-KEY
#    replacements.txt itself is a secret-bearing file: keep it local, delete after.
git filter-repo --replace-text replacements.txt

# 2. Verify the value is gone from every blob (no output == clean):
git log --all -G'AIzaSy[0-9A-Za-z_\-]{30,}' --oneline -- sentinel/.env.example

# 3. Force-push the rewritten history (coordinate with ALL collaborators first):
git push --force --all
git push --force --tags
```

Alternative: the [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)
(`bfg --replace-text replacements.txt`).

**After the force-push**, every collaborator must re-clone or hard-reset; old
clones and forks still contain the value, which is *another* reason rotation
(§3.5) is the real fix.

### 3.7 Preventing recurrence

- `tests/test_secret_scan.py` fails the build if a key-shaped string, a real
  `.env`, or a non-placeholder `*.env.example` value re-enters the tracked tree.
- Consider a pre-commit hook (e.g. `gitleaks`) and enabling GitHub secret-scanning
  push protection on the remote.
