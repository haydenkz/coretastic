#!/usr/bin/env python3
"""Adopt new stable upstream releases into the working tree and report what needs review.

For each stable release newer than the newest locked version, this locks its
commit, seeds <app>/versions/<version>/ from the newest existing version, drops
versions beyond --keep, and moves the upstream submodule to the newest commit.
It then checks that every board's upstream environment exists, that the seeded
patches apply, and whether upstream changed the dependency or platform settings
the pins were made for. It never commits; .github/workflows/upstream-sync.yml
builds the result and opens a pull request for review.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from prepare import ROOT, config_dir, fetch_commit, lock
from layout import boards, version_key

KEEP = 3
# Settings the pinned lib_deps and platform_packages were resolved against.
PINNED_SETTINGS = ("lib_deps", "platform", "platform_packages")


def github(path):
    request = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def repository_slug(url):
    return url.removeprefix("https://github.com/").removesuffix(".git")


def new_releases(releases, prefix, locked):
    """Stable, orderable release tags newer than every locked version, newest first."""
    newest = max(version_key(version) for version in locked)
    tags = {
        release["tag_name"]
        for release in releases
        if not release["draft"]
        and not release["prerelease"]
        and release["tag_name"].startswith(prefix)
        and version_key(release["tag_name"]) is not None
        and version_key(release["tag_name"]) > newest
    }
    return sorted(tags, key=version_key, reverse=True)


def updated_versions(locked, adopted, keep):
    """Returns (versions newest first, dropped version names)."""
    merged = sorted(adopted + locked, key=lambda entry: version_key(entry["version"]), reverse=True)
    return merged[:keep], [entry["version"] for entry in merged[keep:]]


def tag_commit(repository, tag):
    output = subprocess.check_output(
        ["git", "ls-remote", repository, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], text=True
    )
    refs = dict(reversed(line.split("\t")) for line in output.splitlines())
    # An annotated tag's peeled entry names the commit; a lightweight tag is the commit.
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit:
        raise ValueError(f"{repository}: tag {tag} not found")
    return commit


def project_config(checkout):
    """PlatformIO's resolved configuration, by section."""
    output = subprocess.check_output(
        ["pio", "project", "config", "-d", str(checkout), "--json-output"], text=True
    )
    return {section: dict(options) for section, options in json.loads(output)}


def normalized(value):
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).splitlines()
    return [item.strip() for item in items if item.strip()]


def compare_environments(previous, current, environments):
    """Returns human-readable findings for missing environments and changed pinned settings."""
    findings = []
    for board_id, name in environments.items():
        section = f"env:{name}"
        if section not in current:
            findings.append(f"`{board_id}`: upstream environment `{name}` no longer exists")
            continue
        for setting in PINNED_SETTINGS:
            before = normalized(previous.get(section, {}).get(setting))
            after = normalized(current[section].get(setting))
            if before != after:
                removed = [item for item in before if item not in after]
                added = [item for item in after if item not in before]
                findings.append(
                    f"`{board_id}` `{setting}` changed: "
                    f"removed {removed or 'nothing'}, added {added or 'nothing'}"
                )
    return findings


def worktree(upstream, commit, directory):
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(directory), commit],
        cwd=upstream,
        check=True,
    )


def check_patches(checkout, patches):
    failures = []
    for patch in patches:
        result = subprocess.run(
            ["git", "apply", "--check", str(patch)], cwd=checkout, capture_output=True, text=True
        )
        if result.returncode:
            failures.append((patch.name, result.stderr.strip()))
    return failures


def sync(component, keep, dry_run):
    locked = lock()
    entry = locked[component]
    versions = entry["versions"]
    releases = github(f"repos/{repository_slug(entry['repository'])}/releases?per_page=100")
    tags = new_releases(releases, entry["release_tag_prefix"], [v["version"] for v in versions])
    summary = dict(
        component=component,
        repository=entry["repository"].removesuffix(".git"),
        adopted=[],
        dropped=[],
        attention=[],
        report="",
    )
    if not tags:
        summary["report"] = f"No new stable {component} release after {versions[0]['version']}."
        return summary
    adopted = [dict(version=tag, commit=tag_commit(entry["repository"], tag)) for tag in tags]
    kept, dropped = updated_versions(versions, adopted, keep)
    adopted = [a for a in adopted if a["version"] in {k["version"] for k in kept}]
    summary.update(adopted=[a["version"] for a in adopted], dropped=dropped)
    base = versions[0]  # Seed configuration from the newest existing version.
    environments = {b: profile["environments"][component] for b, profile in boards().items()}
    report = [f"## {component}: adopt {', '.join(summary['adopted'])}", ""]
    upstream = ROOT / component / "upstream"
    with tempfile.TemporaryDirectory(prefix="coretastic-sync-") as temporary:
        base_checkout = Path(temporary) / "base"
        worktree(upstream, fetch_commit(component, base["version"]), base_checkout)
        previous = project_config(base_checkout)
        for release in adopted:
            version, commit = release["version"], release["commit"]
            upstream_ref = f"+{commit}:refs/coretastic/{version}"
            subprocess.run(
                ["git", "fetch", "--quiet", "--no-tags", entry["repository"], upstream_ref],
                cwd=upstream,
                check=True,
            )
            checkout = Path(temporary) / version
            worktree(upstream, commit, checkout)
            patches = sorted((config_dir(component, base["version"]) / "patches").glob("*.patch"))
            failures = check_patches(checkout, patches)
            findings = compare_environments(previous, project_config(checkout), environments)
            report += [
                f"### {version}",
                "",
                f"- Commit: [`{commit[:12]}`]({entry['repository'].removesuffix('.git')}"
                f"/commit/{commit})",
                f"- Configuration seeded from `{base['version']}`",
            ]
            if failures:
                for name, error in failures:
                    report.append(f"- **Patch `{name}` does not apply:**")
                    report += ["  ```", *("  " + line for line in error.splitlines()), "  ```"]
                summary["attention"].append(f"{version}: patches need rebasing")
            else:
                count = f"{len(patches)} patch" + ("" if len(patches) == 1 else "es")
                report.append(f"- {count} from `{base['version']}` apply cleanly")
            if findings:
                report += [f"- **{finding}**" for finding in findings]
                summary["attention"].append(f"{version}: upstream build settings changed")
            else:
                report.append(
                    f"- Every board's upstream environment exists with unchanged "
                    f"{', '.join(PINNED_SETTINGS)}, so the existing pins still apply"
                )
            report.append("")
        for directory in Path(temporary).iterdir():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(directory)], cwd=upstream, check=True
            )
    if dropped:
        report += [f"Dropped to keep {keep} versions: {', '.join(f'`{v}`' for v in dropped)}", ""]
    summary["report"] = "\n".join(report)
    if dry_run:
        return summary
    for release in adopted:
        target = config_dir(component, release["version"])
        if not target.exists():
            shutil.copytree(config_dir(component, base["version"]), target)
    for version in dropped:
        shutil.rmtree(config_dir(component, version), ignore_errors=True)
    locked[component]["versions"] = kept
    (ROOT / "upstream-lock.json").write_text(json.dumps(locked, indent=2) + "\n")
    # The submodule tracks the newest locked version.
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", kept[0]["commit"]], cwd=upstream, check=True
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["meshcore", "meshtastic"])
    parser.add_argument("--keep", type=int, default=KEEP, help="Versions to keep per app")
    parser.add_argument("--dry-run", action="store_true", help="Report without changing files")
    parser.add_argument("--summary", type=Path, help="Write a JSON summary for the workflow")
    args = parser.parse_args()
    summary = sync(args.component, args.keep, args.dry_run)
    print(summary["report"])
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
