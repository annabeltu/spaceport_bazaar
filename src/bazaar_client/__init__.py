"""
Our Spaceport Bazaar client. It plays the practice exercise as station P01.

Run it with:  PYTHONPATH=src python -m bazaar_client --credentials PATH

One module per job. The letter is the work package that writes each one
(see the project plan). Package A wrote every module's public names first,
as "contracts", so the other packages could be built at the same time.

    models.py       A  shared data types
    errors.py       A  shared exceptions
    codec.py        D  bytes <-> protobuf messages
    commands.py     D  builds the six client commands
    guards.py       E  checks every message before it is sent
    state.py        F  the client's view of the world
    credentials.py  G  reads P01's token from the server's credentials file
    connection.py   G  the WebSocket connection, headers and subprotocol
    engine.py       H  decides the next move
    runner.py       I  the main read loop
    logs.py         I  logging with the token hidden
    __main__.py     I  the command line

How one message flows through them:

    receive -> codec.decode -> state.apply_server_message -> engine.decide
            -> guards.check -> send -> state.record_ready (after `ready`)
                                       or state.record_sent (after a command
                                       with a request_id)
"""
