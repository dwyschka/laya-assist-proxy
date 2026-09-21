# Running it: develop on the share, run on the device

The code is edited on the SMB share (`/Volumes/home/laya-assist-proxy`), but the
service runs from a local copy (`~/Services/laya-assist-proxy`). `deploy.sh`
moves one to the other and restarts the service:

```sh
./service/deploy.sh                 # copy + restart + wait until ready
LAYA_ASSIST_TARGET=/some/where ./service/deploy.sh
```

It reports when the model is loaded, or points at the log if it is not.

**Why copy at all.** Two hard constraints, both found the hard way:

* launchd on this machine may not *execute* a file from the share —
  `Operation not permitted`, exit 126. The local Python may read the share;
  launchd may not launch anything from it.
* A LaunchAgent starts at login, and at that point the share is not mounted yet.
  Running locally removes the dependency entirely: after a reboot the service
  comes up whether the share is there or not.

**Configuration belongs to the device, not to the working copy.** `deploy.sh`
never overwrites `project.yaml`, `secrets.local.json` or `config.json` on the
target — your catalog, threshold and token survive every deploy. If they are
missing there (first deploy), they are seeded from the source once.

## Installing the service

```sh
./service/deploy.sh                                     # creates ~/Services/laya-assist-proxy
cp service/de.sensou.laya-webui.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.sensou.laya-webui.plist
```

The plist points at `~/Services/laya-assist-proxy/service/start-laya.sh`; adjust
it if you deploy somewhere else.

| command | purpose |
|---|---|
| `launchctl kickstart -k gui/$(id -u)/de.sensou.laya-webui` | restart |
| `launchctl bootout gui/$(id -u)/de.sensou.laya-webui` | stop |
| `launchctl print gui/$(id -u)/de.sensou.laya-webui` | state, pid, last exit code |

Logs: `~/Library/Logs/laya-webui.log` and `.err.log`.

The working directory decides where `project.yaml` and `secrets.local.json` are
read from (`settings.project_root`), so `start-laya.sh` `cd`s into the project
before starting. `LAYA_ASSIST_HOME` overrides it.
