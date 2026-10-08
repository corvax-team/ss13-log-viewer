# ss13-log-viewer

Web viewer for the per-round JSON logs a /tg/-based SS13 server writes. Admins log in with the Discord account linked in SS Central, pick a round, filter by text, ckey, character, category and time, read the surrounding lines and export a player's timeline.

Logs are read in place from the server's log directory, nothing is copied or indexed elsewhere.

## Settings

| Variable | Meaning |
|---|---|
| `LOGS_DIR` | log root with `YYYY/MM/DD/round-N` folders, default `/logs`. Several roots can be joined with `:` when instances share one round numbering |
| `PUBLIC_URL` | address the viewer is reached at, used for the OAuth redirect |
| `ROOT_PATH` | path prefix when served behind a reverse proxy, for example `/logs` |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | Discord application for login, add `<PUBLIC_URL>/auth/callback` as a redirect |
| `SSCENTRAL_URL` | SS Central API, used to turn a Discord id into a ckey |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | game database, read access to the `admin` table decides who may log in |
| `SESSION_SECRET` | random string that signs the session cookie |

## Map view

`/round/N/map` plots events with coordinates on the station map, with a time range, a player track and the same filters as the search. Maps are rendered on startup with SpacemanDMM `dmm-tools` from the game's build directory into `MAPS_DIR`, one version per map file hash.

| Variable | Meaning |
|---|---|
| `GAME_DIR` | the live game build, with `tgstation.dme` and `_maps`, default `/game` |
| `MAPS_DIR` | where rendered maps are stored, default `/maps` |
| `DMM_TOOLS` | path to the `dmm-tools` binary, default `/usr/local/bin/dmm-tools` |
