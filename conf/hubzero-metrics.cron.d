# HUBzero metrics pipeline
# Format: min hour dom month dow user command
# Every 5 min: updates whoisonline map; at :30 past each hour also runs the metrics pipeline.
*/5 * * * * apache  python3 /opt/hubzero/bin/hzmetrics.py tick
