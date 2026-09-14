# Updating pinned dependencies

Upstream submodules and `upstream-lock.json` must identify the same commits. Select an upstream release after checking its V4 board definitions, storage operations, OTA/reset paths, Arduino SDK generation, and application size. A new ESP-IDF generation needs a bootloader compatibility review. Do not accept an upstream submodule bump merely because its unmodified firmware builds.

The integration environments pin the resolved library set, including transitive registry dependencies. `dependencies.json` records that set for review. Keep it synchronized with `integration.ini`. PlatformIO framework/tool versions, Python dependencies, Node dependencies, and GitHub Actions are pinned separately. Dependency update PRs must pass the actual integrated builds and isolation tests.

Patches apply to disposable `.build/<component>` checkouts in lexical filename order. The build script fingerprints the patch/configuration inputs and replaces its generated checkout when they change. Never edit the source submodule to carry integration changes. Generate a patch against the pinned revision and keep it beside the submodule.

`partitions.csv`, the C++ write boundaries, both manifest validators, and the layout/storage epoch are a compatibility contract. Changing offsets or storage semantics requires updating all consumers, tests, and recovery instructions. Existing component updates must fail closed when their storage/layout contract is incompatible.

The release workflow builds before publishing. It stores one manifest alongside the binaries and publishes the same files under the static flasher's `releases/` directory. Source archives contain the checked-out upstream sources and our patches, the built library sources, web production dependencies, and SDK packages. Linker maps and ELF files are retained separately as build metadata. Run the source-bundle command after all three firmware builds and `npm ci`.

Tag releases only after reviewing the build artifacts and the applicable hardware validation record. GitHub Pages must use the Actions deployment source. The workflow does not automatically create tags or merge dependency updates.

## Building from a source archive

Extract `corresponding-source.tar.gz` into a new directory. Its `bundles/` directory contains clean Git bundles for the project and pinned upstream/submodule histories. Reconstruct the checkout before running the README build commands:

```sh
python3 coretastic/scripts/release/restore_git.py --source . --output rebuilt
cd rebuilt
```

The archive also includes the resolved firmware libraries, web production packages, and ESP-IDF/Arduino SDK source packages under `dependencies/` and `sdk/`. Normal build commands still download/install pinned build tools and dependencies; this archive is source material, not an offline toolchain installer.
