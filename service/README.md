# Running as a service (macOS, launchd)

`de.sensou.laya-webui.plist` starts the proxy at login and restarts it if it
dies. Install:

```sh
mkdir -p ~/Library/Application\ Support/laya
cp service/start-laya.sh ~/Library/Application\ Support/laya/
chmod +x ~/Library/Application\ Support/laya/start-laya.sh
cp service/de.sensou.laya-webui.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.sensou.laya-webui.plist
```

| command | purpose |
|---|---|
| `launchctl kickstart -k gui/$(id -u)/de.sensou.laya-webui` | restart |
| `launchctl bootout gui/$(id -u)/de.sensou.laya-webui` | stop |
| `launchctl print gui/$(id -u)/de.sensou.laya-webui` | state, pid, last exit code |

Logs: `~/Library/Logs/laya-webui.log` and `.err.log`.

## Two things that cost time here

**The start script has to live on local disk.** A launchd job on this machine is
not allowed to *execute* a file from the SMB share — `Operation not permitted`,
exit 126. The local Python may read the share, launchd may not launch anything
from it. For the same reason the plist carries no `WorkingDirectory` pointing at
the share; the script changes into it itself.

**A LaunchAgent starts at login, not at boot.** Without automatic login, nothing
runs after a reboot until somebody logs in. A LaunchDaemon would start earlier
but could not reach the share — it is mounted with the user session. If the share
is not there yet, the script exits and launchd retries every 30 seconds
(`ThrottleInterval`) until it appears.

The working directory decides where `project.yaml` and `secrets.local.json` are
read from (`settings.project_root`), so the script `cd`s into the project before
starting. `LAYA_ASSIST_HOME` overrides it.
