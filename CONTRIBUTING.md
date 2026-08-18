# Contributing to this code base
We want to make contributing to this project as easy and transparent as
possible.

## Development setup
After cloning, run:

```sh
./scripts/setup-dev.sh
```

This configures a few per-clone git settings — most importantly a `pre-push`
hook. Git does not allow a repository to install its own hooks at clone time,
so this step cannot be automated away.

Verify at any point with `./scripts/setup-dev.sh --check`.

If you have no `internal` remote configured, this is close to a no-op — the
push guard has nothing to guard and stays out of your way.

## Branch naming
Branch names beginning with `int/` are reserved. Pushes of `int/*` refs to this
repository are rejected server-side, and pull requests opened from an `int/*`
head branch fail CI.

The Meta research group maintains cluster-specific configuration on branches in
that namespace. Reserving the prefix is what lets those branches carry a file at
the *same path* as its public counterpart — `config/mode/slurm_cluster.yaml`,
for instance — instead of renaming files or maintaining a parallel checkout.
None of that affects contributing here: work from `main`, name your branch
anything that isn't `int/*`, and you will never encounter these checks.

## Pull Requests
We actively welcome your pull requests.

1. Fork the repo and create your branch from `main`.
2. If you've added code that should be tested, add tests.
3. If you've changed APIs, update the documentation.
4. Ensure the test suite passes.
5. Make sure your code lints.
6. If you haven't already, complete the Contributor License Agreement ("CLA").

## Contributor License Agreement ("CLA")
In order to accept your pull request, we need you to submit a CLA. You only need
to do this once to work on any of Facebook's open source projects.

Complete your CLA here: <https://code.facebook.com/cla>

## Issues
We use GitHub issues to track public bugs. Please ensure your description is
clear and has sufficient instructions to be able to reproduce the issue.

Facebook has a [bounty program](https://www.facebook.com/whitehat/) for the safe
disclosure of security bugs. In those cases, please go through the process
outlined on that page and do not file a public issue.

## License
By contributing to this code, you agree that your contributions will be licensed
under the LICENSE file in the root directory of this source tree.