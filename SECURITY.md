# Security policy

## Supported versions

qpel is pre-1.0. Security fixes are made on `main` and included in the next release.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately through GitHub: on the repository's **Security** tab, choose
**Report a vulnerability**. If that is not available, contact the maintainer through
[GitHub](https://github.com/shatalov-alex) and ask for a private channel.

Please include what is affected, how to reproduce it, and its impact. You can expect an
acknowledgement within a week and a status update within two weeks.

## Scope

qpel reads untrusted source code (CUDA, Metal, qpel text and IR) and generates source and
host code that is later compiled and run. Relevant issues include:

- generated code that reads or writes memory outside the buffers it was given;
- validation that accepts a program it should reject, in a way that leads to the above;
- importer or build steps that run commands or write files outside their output directory.

Bugs in third-party toolchains (nvcc, Metal, GPGPU-Sim) should go to their maintainers;
we are glad to help report them.
