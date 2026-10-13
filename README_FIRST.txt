DDR Step Analyzer - Read me first
==========================================

This tool looks at a video of you playing DDR and finds your skeleton (body joints).
It shows where you really stepped, how your balance point moves, and how you use
your heels and toes, and then gives you advice on how to improve.

- Everything runs on your own PC. Your videos and data are never sent anywhere.
- No AI subscription or paid service is needed (it uses only free, open-source software).
- You need the internet only for the first setup.


# What you need
------------------------------------------
- Windows 10 or 11 (64-bit)
- 8 GB of memory or more
- 3 GB of free disk space
- An internet connection for the first setup


# Install (only the first time)
------------------------------------------
1. Right-click the ZIP file -> "Extract All".
   Good place: C:\ddr-step-analyzer
   (Avoid the Desktop and OneDrive folders. Syncing can make the tool slow.)

2. In the extracted folder, double-click "setup.bat".

   * If a blue "Windows protected your PC" screen appears,
     click "More info" -> "Run anyway".

3. A black window installs everything automatically (about 10-20 minutes).
   - If Python is not installed, it is installed for you (no administrator rights needed).
   - The skeleton detection models (about 360 MB) are downloaded.

4. When you see "Setup complete", press any key to close the window.
   A shortcut called "DDR ステップ解析" (DDR Step Analyzer) is now on your desktop.


# How to use
------------------------------------------
1. Double-click "DDR ステップ解析" on the desktop.
   (A black window opens, and after a moment the app opens in your browser.)
   * The black window is the analysis server. Do not close it while a video is being analyzed.

2. The app uses the language of your browser (English, Japanese or Chinese).
   You can change it with the menu at the top right.

3. Click "+ Add video" at the top right -> choose a video file or paste its path,
   then click "Start analysis".
   - A 2-minute video takes about 20-40 minutes (it depends on your PC).
   - If you are in a hurry, set "Detection quality" to "Fast". It finishes in a few minutes
     (but it is a little less accurate).
   - While a video is being analyzed, the list at the top shows the progress.

4. When the analysis is done, the skeleton and the balance point are shown on top of the video.
   - Top view of the pad: which panel you stepped on with which foot, and where your balance point is
     (it turns automatically to match the video, so videos filmed from the front also work.
      If the direction is wrong, click the "↻" button at the top right of the top view
      to turn it 90° at a time.)
   - Timeline: balance point sway, hip height, landings, panel lights
   - Advice: what to improve and how to practice
   - Balance events: moments where your balance point moved a lot (click one to watch it in slow motion)
   - Landings: whether you hit each panel with your toe or heel, and heel-up steps

5. If the grid does not match the pad, click "Calibrate pad",
   drag the corners of the 3x3 grid to the edges of the pad, and click "Apply".
   (Mouse wheel: zoom. Double-click: reset the view.)

6. Use the "This video:" row below the play button for settings for each video
   (the video is analyzed again as soon as you change one).
   - Machines whose panels do not light up when you step on them (for example, old white cabinets)
     -> uncheck "Panels light up"
     (Light from the speakers will no longer be mistaken for panel lights. An AI model, trained on
      videos from machines with panel lights, decides the panels from the skeleton. It is less
      accurate than videos with panel lights.)
     * Videos where no panel lights can be found (for example, machines with white light frames)
       switch to this mode automatically.
   - A hand is not in the video
     -> check "Left hand not in video" or "Right hand not in video"


# How to quit
------------------------------------------
Just close the browser tab. The black window closes by itself after about 10 seconds.
(If a video is still being analyzed, it waits until the analysis is finished.)


# Tips for filming
------------------------------------------
- Film from behind and above the player at an angle, with the phone at about waist height.
- Show the whole pad and the whole body (the upper body too, if you can).
- Keep the phone still.
- It is OK to pick up the phone before or after playing (for example, to film the result screen).
  Parts without the pad and feet are left out of the analysis automatically.
- Videos filmed from the side at floor level cannot be analyzed, because the pad is not visible.


# Problems
------------------------------------------
- setup.bat stopped, or red text appeared
  -> Check your internet connection and run setup.bat again.
     If it still fails, please send a photo of the screen.

- The browser does not open
  -> Open http://127.0.0.1:8765 in your browser.

- "The server has stopped" appears at the top of the page
  -> Double-click "DDR ステップ解析" on the desktop again.

- The analysis is very slow
  -> Set "Detection quality" to "Fast" when you add the video.

- Uninstall
  -> Delete these:
     - This folder
     - The desktop shortcut "DDR ステップ解析"
     - The Python environment: C:\Users\(your user name)\AppData\Local\ddr-pose
     - The models (about 360 MB): C:\Users\(your user name)\.cache\rtmlib

More details (with pictures): README.en.md, or
https://github.com/xkamome/ddr-step-analyzer/blob/main/README.en.md
