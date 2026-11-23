# Contributing to OptiDay

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Security problems go through private reporting, not public issues. See [SECURITY.md](SECURITY.md).
- Never commit real activity data, screenshots of your own screen, emails, names or keys.
  Demo and test data must be made up.

## Running the tests

```sh
python3 -m pip install -e '.[dev]'
python3 -m pytest
```

## License of contributions

OptiDay is licensed under the [AGPL-3.0](LICENSE), and the maintainer also offers it under a
separate commercial license. So that both stay possible, by submitting a contribution you agree
that:

1. your contribution is licensed under the AGPL-3.0, and
2. you grant the OptiDay maintainer a perpetual, worldwide, royalty-free, irrevocable license to
   use, modify, sublicense and relicense your contribution, including under other licenses.

You also confirm that you wrote the contribution or otherwise have the right to submit it.
Please add a `Signed-off-by:` line to your commits (`git commit -s`) to show this.
