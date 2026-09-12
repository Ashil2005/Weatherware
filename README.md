<img width="1280" height="640" alt="git (1)" src="https://github.com/user-attachments/assets/8920b256-2ba8-4988-b824-5351134eb4bd" />



# [Project Name] 🎯


## Basic Details
### Team Name: [ASHIL]


### Team Members
- Team Lead: [ASHIL S MATHEWS] - [Mar Baselios Christian College of Engineering & Technology (MBCCET)]


### Project Description
Weatherware is a desktop app that simulates weather right on top of whatever you're doing. Sun, rain, wind, and snow randomly roll across your screen — and they don't just *look* real, they hijack your screen brightness and your laptop's fan to make you actually *feel* the weather. Hidden inside is a rare secret event that nobody sees coming.


### The Problem (that doesn't exist)
Your desktop is emotionally flat. It has never once rained on your spreadsheet. Your laptop fan has no dramatic timing. You can work for hours and the weather outside your window has zero effect on your screen — a tragedy nobody has ever complained about.


### The Solution (that nobody asked for)
We gave your desktop its own weather system that you cannot control and did not consent to. Sunny days crank your brightness to maximum, glare a blazing sun over your desktop, and switch your fan to quiet mode so the laptop genuinely warms up. Rain drops your brightness, roars your fan to full speed for real cooling, flickers your desktop icons with lightning, and fogs up your screen so you have to physically wipe it with your mouse to see your work. Wind blows leaves across your monitor; snow piles up at the bottom. It is a fully-featured productivity disruptor, and it works exactly as badly as intended.


## Technical Details
### Technologies/Components Used
For Software:
For Software:
- **Language:** Python
- **Framework:** Pygame (rendering + audio mixing)
- **Libraries:** pygame, screen-brightness-control, wmi, pywin32, comtypes, keyboard, pystray, Pillow, psutil, pyinstaller
- **Windows internals:** a transparent, click-through, always-on-top overlay (per-pixel-alpha layered window via the Win32 API); Windows Power Mode control (`PowerSetActiveOverlayScheme`); Lenovo GAMEZONE WMI for real fan-mode switching; UAC auto-elevation
- **Tools:** Claude Code (AI pair-programmer for the whole build), AI image generation for the wedding artwork, remove.bg for transparency, OBS for the demo

For Hardware:
- No external hardware — but it treats your laptop's **screen brightness** and **cooling fan** as output devices, which is arguably worse.


### Implementation
For Software:
# Installation
git clone https://github.com/Ashil2005/Weatherware.git
cd Weatherware
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# Run
.venv\Scripts\python.exe main.py
Runs best on Windows. On startup it requests Administrator (via a UAC prompt) so it can control the Lenovo fan mode — click Yes. On non-Lenovo machines it simply skips the fan control and everything else still works.

**Controls (global hotkeys):**
- `Ctrl+Shift+1` → Sunny  |  `Ctrl+Shift+2` → Rainy  |  `Ctrl+Shift+4` → Windy  |  `Ctrl+Shift+5` → Snowy
- `Ctrl+Shift+3` → the secret rare event 👀
- `Ctrl+Shift+M` → mute  |  `Ctrl+Shift+Q` → clear the weather and quit (restores brightness/fan/power)

## The Weather (and one thing we're not going to tell you about)

Weatherware ships with **five** kinds of weather:

- **☀️ Sunny** — brightness maxes out, a glowing animated sun with heat-mirage shimmer, fair-weather clouds, and the fan goes quiet so the laptop coasts warm.
- **🌧️ Rainy** — parallax rain, rolling dark storm clouds, brightness drops, fan roars to full (real cooling), lightning that flickers your actual desktop icons, and condensation fog you wipe away with your cursor.
- **🌬️ Windy** — clouds and leaves streak across the screen in a random direction, fan gusting along.
- **❄️ Snowy** — falling snow that accumulates at the bottom, coldest brightness, fog, and full-cooling fan.

...and then there is a **sixth, rare, secret event.**

> ### ⚠️ SPOILER WARNING ⚠️
> This project has a hidden surprise — the whole heart of the thing — and it is *so much better* if you see it before you read about it.
>
> **👉 Please watch the demo video first (linked below). Let it surprise you. Then come back and read the next section.**
>
> Seriously. Go watch. We'll wait. 🌦️
>
> <details>
> <summary><b>🚨 Click here ONLY after watching the demo — folklore spoiler inside 🚨</b></summary>
>
> <br>
>
> There's an old saying in Kerala: when it rains while the sun is still shining, it means **the fox and the hen are getting married** — *കുറുക്കന്റെയും കോഴിയുടെയും കല്യാണം.* Remarkably, Japan has the exact same belief about the exact same sky — they call a sunshower a *fox's wedding* (*kitsune no yomeiri*).
>
> So we made it real. Once in a rare while, Weatherware triggers a **sunshower** — sun and rain together — and after a few seconds, a full **fox-and-hen wedding** unfolds on your desktop: a fox groom, a hen bride, a goat priest, and four rows of animal guests, under a flower arch, complete with wedding bells, the wedding march, *"I now pronounce you husband and wife,"* the kiss, and the whole crowd cheering. Then it quietly fades back into the weather.
>
> It is completely useless. It is also the reason this project exists.
>
> </details>
  

### Project Documentation
For Software:

For Software:

### Screenshots
![https://drive.google.com/file/d/16XAtD7RSmPkboQh--H1SVoK0zMOl8FRo/view?usp=sharing](screenshots/sunny.png)
*Sunny weather taking over the desktop — brightness at max, animated sun and god-rays, heat shimmer, and the fan switched to quiet mode.*

![https://drive.google.com/file/d/1vLD1ieDENnpzkFW_ua2xXFBdvRGYcL2K/view?usp=sharing](screenshots/rainy.png)
*Rainy weather over live apps — storm clouds, parallax rain, condensation fog you wipe with the cursor, and the fan roaring for real cooling.*

![https://drive.google.com/file/d/15I1Q3uzpg5dPGwJbMa4-YWSczuLiAcOT/view?usp=sharing](screenshots/snowy.png)
*Snowy weather — falling snow accumulating at the bottom of the screen with the display dimmed to its coldest.*

### Diagrams
![https://drive.google.com/file/d/1oPkOjqdCz1whuWQU2nwy5dYGEWiXRuwE/view?usp=sharing](screenshots/architecture.png)
*Architecture: a scheduler/state-machine picks a random weather, each scene drives real hardware effects (brightness, power mode, Lenovo fan) plus a transparent click-through overlay for visuals and layered audio — and rarely fires the hidden sunshower wedding sequence.*

### Project Demo
# Video
[https://drive.google.com/file/d/1mQFoU_kNEiQOdbPEm_RpiJzsRtOLsciY/view?usp=sharing]
*The video walks through all five weather types disrupting a real desktop — brightness, fan, lightning, and the wipe-away fog — and ends by revealing the hidden fox-and-hen sunshower wedding.*
# Additional Demos
[[Add any extra demo materials/links](https://drive.google.com/file/d/1mQFoU_kNEiQOdbPEm_RpiJzsRtOLsciY/view?usp=sharing)]

## Team Contributions
- [ASHIL S MAHTEWS]: [Solo project.]


---
Made with ❤️ at TinkerHub Useless Projects 

![Static Badge](https://img.shields.io/badge/TinkerHub-24?color=%23000000&link=https%3A%2F%2Fwww.tinkerhub.org%2F)
![Static Badge](https://img.shields.io/badge/UselessProjects--26-26?link=https%3A%2F%2Ftinkerhub.org%2Fevents%2F1M8ORET9A1%2Fuseless-projects-3.0)



