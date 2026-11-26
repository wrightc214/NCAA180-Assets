"""Check open PRs and publish a coordinated promotion with compensating cleanup."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def api(path, token=None, method="GET", payload=None):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(os.environ.get("GITHUB_API_URL", "https://api.github.com") + path,
                      data=data, method=method, headers=headers)
    with urlopen(request, timeout=45) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def pages(path, token=None, **query):
    page = 1
    while True:
        result = api(path + "?" + urlencode({**query, "page": page, "per_page": 100}), token)
        yield from result
        if len(result) < 100:
            return
        page += 1


def check_duplicates(asset_repo, app_repo, rows):
    checks = {
        asset_repo: {row[column].casefold() for row in rows
                     for column in ("transfer_path", "master_path", "archived_master_path")
                     if row[column]},
        app_repo: {row["app_path"].casefold() for row in rows},
    }
    for repo, expected in checks.items():
        token = os.environ.get("GITHUB_TOKEN") if repo == asset_repo else None
        for pr in pages(f"/repos/{repo}/pulls", token, state="open"):
            for entry in pages(f"/repos/{repo}/pulls/{pr['number']}/files", token):
                paths = {entry[field].casefold() for field in ("filename", "previous_filename")
                         if entry.get(field)}
                if paths & expected:
                    raise ValueError(f"Open PR overlaps this promotion: {pr['html_url']}")
            if api(f"/repos/{repo}/pulls/{pr['number']}", token)["changed_files"] > 3000:
                raise ValueError(f"Cannot inspect every changed path in {pr['html_url']}")


@dataclass
class Target:
    repo: str
    checkout: Path
    branch: str
    token: str
    sha: str = ""
    push_attempted: bool = False
    pr_number: int | None = None

    @property
    def endpoint(self):
        return f"/repos/{self.repo}"

    @property
    def ref(self):
        return "heads/" + quote(self.branch, safe="/")

    def git(self, *args, token=None):
        env = {key: value for key, value in os.environ.items()
               if key not in {"APP_PROMOTION_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}}
        if token:
            env.update(GH_TOKEN=token, GIT_TERMINAL_PROMPT="0")
        return subprocess.check_output(["git", "-C", str(self.checkout), *args], env=env)

    def push(self):
        self.git("-c", "user.name=github-actions[bot]",
                 "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-m", "Promote artwork from Transfer")
        self.sha = self.git("rev-parse", "HEAD").decode().strip()
        ref = "refs/" + self.ref
        self.push_attempted = True
        self.git("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                 "push", "--force-with-lease=" + ref + ":", "origin", f"HEAD:{ref}", token=self.token)

    def open_pr(self, body):
        return api(self.endpoint + "/pulls", self.token, "POST", {
            "title": "Promote artwork from Transfer", "head": self.branch, "base": "main",
            "body": body, "draft": False,
        })

    def cleanup(self):
        from urllib.error import HTTPError

        errors = []
        numbers = {self.pr_number} if self.pr_number is not None else set()
        try:
            owner = self.repo.split("/", 1)[0]
            prs = pages(self.endpoint + "/pulls", self.token, state="open",
                        head=f"{owner}:{self.branch}")
            for pr in prs:
                if pr["head"]["sha"] != self.sha:
                    continue
                numbers.add(pr["number"])
        except Exception as error:
            errors.append(f"find open PR: {error}")

        for number in sorted(numbers):
            try:
                api(f"{self.endpoint}/pulls/{number}", self.token, "PATCH", {"state": "closed"})
            except Exception as error:
                errors.append(f"close PR #{number}: {error}")

        try:
            ref = api(f"{self.endpoint}/git/ref/{self.ref}", self.token)
            if ref["object"]["sha"] != self.sha:
                raise RuntimeError("branch points to a different commit")
            api(f"{self.endpoint}/git/refs/{self.ref}", self.token, "DELETE")
        except HTTPError as error:
            if error.code != 404:
                errors.append(f"find or delete branch: {error}")
        except Exception as error:
            errors.append(f"find or delete branch: {error}")
        if errors:
            return [f"{self.repo} branch {self.branch}, PR numbers {sorted(numbers) or 'unknown'}: {e}"
                    for e in errors]
        return []


def publish(targets, body):
    try:
        for target in targets:
            target.push()
        for target in targets:
            pr = target.open_pr(body)
            target.pr_number = pr["number"]
            print(f"{target.repo}: {pr['html_url']}", flush=True)
    except Exception as error:
        failures = []
        for target in reversed(targets):
            if target.push_attempted:
                failures.extend(target.cleanup())
        if failures:
            raise RuntimeError("Promotion failed; incomplete cleanup:\n" + "\n".join(failures)) from error
        raise RuntimeError("Promotion failed; promotion PRs and branches cleaned up") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path)
    parser.add_argument("app", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    raw = args.manifest.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    if not manifest["files"]:
        return
    from promote_assets import assert_changes, authorized_paths
    asset_paths, app_paths = authorized_paths(manifest["files"])
    assert_changes(args.asset, asset_paths)
    assert_changes(args.app, app_paths)
    asset_repo = os.environ["GITHUB_REPOSITORY"]
    config = json.loads((args.asset / "config/promotion.json").read_text(encoding="utf-8"))
    suffix = f'{int(os.environ["GITHUB_RUN_ID"])}-{int(os.environ["GITHUB_RUN_ATTEMPT"])}'
    targets = [Target(asset_repo, args.asset, f"promotion/{suffix}", os.environ["GITHUB_TOKEN"])]
    if app_paths:
        if not os.environ.get("APP_PROMOTION_TOKEN"):
            raise ValueError("APP_PROMOTION_TOKEN is required to publish App changes")
        targets.append(Target(config["app_repo"], args.app, f"assets/promotion-{suffix}",
                              os.environ["APP_PROMOTION_TOKEN"]))
    body = "Review the promotion manifest before merging.\n\n````json\n" + raw + "````\n"
    if len(body) > 65536:
        raise ValueError("Promotion manifest exceeds GitHub's PR body limit")
    publish(targets, body)


if __name__ == "__main__":
    main()
