Build a small, genuinely incremental local data warehouse by following the ducktail skill.

Read the skill's `SKILL.md` (path given above) and follow it. Use ONLY the skill and its own
`assets/template/` and `references/` -- do not look elsewhere in the repository.

Scaffold the template into the current working directory, then add ONE source over the USGS
earthquake feed, which supports a real since-query:

  https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=<ISO8601>&endtime=<ISO8601>&minmagnitude=4.5&orderby=time

Each GeoJSON feature has `id`, `properties.mag`, `properties.place`, `properties.time` (epoch
MILLISECONDS), and `geometry.coordinates` = `[lon, lat, depth_km]`.

Model it as an incremental `merge` table: primary key the event `id`, cursor the event `time`
stored as epoch SECONDS (divide the ms by 1000), an `initial` window of ~14 days, and a small
`overlap` (about 1 hour). Crucially, `produce(since)` must pass the harness-provided (already
overlap-rewound) `since` as `starttime=<since as ISO8601>` to the USGS query, with
`endtime=<now>`, so the SERVER returns only events in `[since, now]`. Do not re-subtract the
overlap. A second run must therefore pull only the small overlap window from the network.

Run `uv run refresh.py` once and confirm it loaded rows. You do not need to build a dashboard.
Keep everything in the current working directory. Leave the investigation in place when done --
an automated checker will then wipe the store, run the refresh twice, and verify that the second
run fetched far fewer rows than the first, with no duplicate rows.
