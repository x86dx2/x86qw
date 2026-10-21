# Security policy

## Supported versions

The version marked **Latest** in [GitHub Releases](https://github.com/x86dx2/x86qw/releases/latest) receives security fixes. Historical versions and nightly snapshots can be useful for reproduction, but come with no guarantee of a backported fix.

## Reporting a vulnerability

Use the **Report a vulnerability** button on the [Security](https://github.com/x86dx2/x86qw/security) tab to open a private advisory. Do not publish an issue, discussion, pull request or proof of concept before the fix is coordinated.

Include, when possible:

- the affected component and version;
- the operating system and architecture;
- preconditions and the impact observed;
- minimal steps to reproduce;
- sanitized logs and a suggested mitigation;
- any disclosure plan or deadline already agreed with third parties.

## Priority scope

- validation of packages, hashes and catalogs;
- path traversal, links and writes outside the target;
- execution of untrusted commands or arguments;
- leaks of passwords, tokens or private configuration;
- locks, journals and recovery that could reach other processes or files;
- exposure of MVDSV, QTV or QWFWD beyond the requested `--bind`;
- workflows, publishing and the supply chain.

Flaws only in upstream software can also affect x86QW. Send the report here if the distribution, configuration or packaging widens the impact; coordination with upstream will happen without exposing the researcher.

## Expected process

The project will acknowledge the advisory, validate the scope, prepare the fix and agree on disclosure. No financial reward is promised. Good-faith reports that avoid downtime, access to third-party data and premature exposure are welcome.
