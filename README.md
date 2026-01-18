# romm_sync

A utility for syncing emulator save files across your gaming devices and backing them up to [ROMM.app](https://romm.app/).

## Overview

I've found that Romm is a nice way to have a "source of truth" for my game library, but for now, full-featured clients like [Grout](https://github.com/rommapp/grout) are not supported for all handhelds and Romm's planned sync feature is not ready (yet). If you have multiple handhelds whose saves are kept in sync with syncthing and want a way to have those in romm so you can switch back and forth at will, **romm_sync** might help! 

This all started when I set up Syncthing between my devices with Retroarch-based OS's (Android, MuOS, Knulli,...) and Minarch-based OS's (MinUI, NextUI) and none of the folders lined up properly and forced me into making a tedious Syncthing folder setup. Doable yes, but annoying. I had my Romm instance sitting right there and an opportunity to practice my Python for work so now...here we are.

### What you'll need:
- A server or NAS of some kind that hosts a central file sync location. I use Syncthing, but technically it could be anything.
- A self hosted instance of Romm that is accessible by the machine on which this app is deployed.
- Docker to deploy the app.

### What this does:
1. Gaming devices sync save files to a central location via Syncthing.
2. `romm_sync` detects new/modified saves and states in this "local library"
3. Matches the contents of your library with the content on your Romm instance.

### Some caveats

#### Limited Scope of Save Support
I wrote this mainly for my uses, so it's almost certainly not taking everybody's folder structure into account. My Retroarch instance is configured similar to the Knulli defaults:
- Sort Saves by Content Directory
- Sort States by Content Directory

#### Possible incompatibilities
It *does not* sort states by core, though I had some leftover different cores for Nintendo DS that seemed to work ok. This will work best with save (.srm) files, and, if you're consistent in the cores you use, .state files should "just work" as well.

The implicit assumption here is that the game files (ROM, with one M) on your device are *already* the same files as the ROM's in your Romm library. Set up your Romm library before trying to run this app. A tip that really helped me is that Romm allows you to map your *local* directory names to *their supported platform names* ("slugs") to make library management easier. [See the docs here.](https://docs.romm.app/latest/Getting-Started/Configuration-File/?h=platform+name#custom-folder-names) 

As far as I can tell, Retroarch, Batocera (Knulli), and EmulationStation share standard content directory naming schemes and I haven't run into any sync issues (yet!). This is one of the areas I'm looking to make improvements on so I welcome feedback.

#### This is NOT a Romm Client. 

It's just a one way push to Romm from Syncthing with the expectation that you use a real romm client to pull from Romm to your handheld. (see Grout, for example). This just makes it easier to have access to Romm's library organization niceties if you have some devices that don't support a Romm client.

## Architecture

```
Gaming Devices (RetroArch, etc.)
         ↓ (Syncthing)
    Central Sync Folder
         ↓ (romm_sync watches)
    ROMM Database (via API)
    + (Optional and Separate) Backup Location
```

## Usage
I tried to make this as hands-off of a setup as possible but I'm a bit of a noob on docker development so this isn't published on any docker repos yet and as a result, it isn't as smooth as it could be.

### Get the Files
Clone or download+unzip this repo onto the machine you'll be running it on.

### Configure
Set the following variables in `.env.example`:

#### ROMM_URL: 
Your romm instance's address. 

#### ROMM_USERNAME: 
Your username in romm.

#### ROMM_PASSWORD: 
Your password for romm.

#### SYNC_FOLDER: 
The location of your synced save files (i.e. the folder containing all your syncthing data).

#### SYNC_MODE: 
Two options here:
- "watch" (default): Runs a full sync when starting the container, then incremental afterward based on changes to files in the `SYNC_FOLDER`
   - `WATCHDOG_DELAY_SECONDS` is the amount of time it'll wait for a period of no file system activity once a change is detected. Default is 60 seconds. This means if you save a state at t=0s, syncthing grabs it and puts it on the server at t=5s, and then you save a state again at t=45s (which syncthing again pushes 5s later), the server will not attempt to sync until t=110s.
      - Highly recommend leaving it at 60s.
- "periodic": runs the full sync between your local library and romm library every `SYNC_INTERVAL_SECONDS`. 
   - Simpler but if you have a large library, syncs may take a while because it queries the ROMM API for every single save and state you have.
   - I have about 40 different games synced with 1 save and a couple states, running on the same machine as the instance, and a full sync with no changes takes ~20 seconds.

### Spin it up!
Run the following from a terminal.
```bash
cd {YOUR_CLONE_DIRECTORY}
docker compose build
docker compose up -d
```

If you run just `docker compose up`, it'll start up in the foreground and show you all the logs as the app starts up.

That oughta be it...now it should just work™ and you should soon see all your saves and states in Romm!

