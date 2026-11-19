# The x86QW layer of KTX 1.47

This directory is a boundary of authorship and provenance, not a tree that is
copied whole into the installation. Each part has a single role:

- `catalog/`: modes and declarative launcher data; the Frogbot profiles stay
  apart in `catalog/frogbots/`;
- `config/`: client configuration, key help and presets the inventory places
  in `qw/`;
- `runtime/`: the QVM and symbol map that are actually packaged;
- `source/`: five numbered patches used to reproduce the runtime (a
  deterministic date, identities/skill, balancing, names per role and the
  optional Quad Coach);
- `policy/`: verifiable rules for composing the `ktx.pk3` layer;
- `CHANGELOG.md`: history of the x86QW integration.

`maintenance/inventory/components.json` declares explicitly which files are
installed, which go into the PK3 and which exist only for building or
validation. None of these subfolders is created in the player's directory.
