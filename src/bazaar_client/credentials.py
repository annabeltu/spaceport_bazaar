"""
Reads P01's token from the server's credentials file
(contract and Credentials by package A, load_credentials() by package G).

The practice server writes `validation-credentials.json` when it starts. Its
`players` list has one entry per station, each with `station_id`, `run_id`
and `token`. (Checked 2026-09-21 by printing only the file's key names.)

The token is a password. It's never printed, logged, put in an error message
or committed.
"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    """What connecting as one station needs.

    `repr=False` on the token means printing this object, or putting it in an
    f-string or a log line, never shows the token. Two things still would, so
    never log them: `dataclasses.asdict(credentials)` and `credentials.token`
    itself.
    """

    station_id: str
    run_id: str
    token: str = field(repr=False)


def load_credentials(path: Path, station_id: str) -> Credentials:
    """The `players` entry in the credentials file at `path` whose station_id
    is `station_id`.

    Reads the file fresh on EVERY call, never a saved copy, because restarting
    the server issues new tokens and a new run_id.

    Raises CredentialsError (from bazaar_client.errors) with a clear message
    that never contains the token when:
    - the file doesn't exist ("start the server first"),
    - no entry has this station_id, or
    - that entry has no token.
    """
    raise NotImplementedError("package G")
