<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
SPDX-License-Identifier: Apache-2.0
-->

# Development changelog

Every build that never went out through the Bespok3d app, kept as it was written at the time: the
releases made before the plugin was published, 0.1.0 to 0.26.1, and the test builds made since, on
a branch, before the release they led to. They were built by hand and installed on the
maintainer's printers. The public changelog, `plugin/doc/CHANGELOG.md`, starts at 0.27.0 and is the
one that ships in the package; this file stays in the repository. `ROADMAP.md` has the reasoning
behind each change.

## 1.0.0, prepared on 2026-11-14

The release the test builds below lead to. Its public entry is in `plugin/doc/CHANGELOG.md`
and covers 0.30.1 to 0.30.7 in one. What 1.0.0 itself changes on the printer is two texts:

- **"Sharp" is no longer "Sharp, cheaper"** in the Enlarging list, and its (i) says that for a
  P3 smooth is the cheaper one (0.30.3 on the U1: 62.3% smooth against 64.9% sharp).
- The manifest's description says what a P3 has and has not been run through, instead of
  "has not been run on a printer yet"; `updated_at` is 2026-11-14.
- 0.30.7 on the U1, a P3, one tile, twice a second: 49.4%, as 0.30.6 read at that setting.
- Docs: both READMEs carry the measured cost of each setting as tables (the state, the live
  view's rate, the readout refresh, sharpening and enlarging), and the P3's hardware status.
  The Raspberry Pi 4 table stays, dated September 2026.
- `plugin/doc/images/settings.jpg` was made again, since it lacked three settings. It is NOT a
  photograph of the printer: the plugin's own page in Chromium on a development machine, fed a
  real P3 frame saved from the U1 on 2026-11-12 (`p3-frame-hot.bin`), and its cost line was
  given the figure measured on the U1 for a P3 as installed, 49.4%, for both of its numbers.
  The one before is in `_trash/before-1.0.0/plugin-doc/`.
- The channel stays `rc`. Promoting to stable later is a change of `channel` in the manifest,
  which the builder's README says is independent of the release kind the Action is given.

## Test builds after 0.30.0, for what a P3 costs

Written on 2026-11-12, the evening the first P3 met the plugin. 0.30.1 passed the gate and was
never built: 0.30.2 followed it within the hour, and was built, installed on the U1 and measured
the same evening. 0.30.3 came out of what that measurement found, and was built, installed and
measured the same evening too. 0.30.4 is the next day's, 2026-11-13, and is about reading the
camera where the first three are about drawing the picture. 0.30.5, 2026-11-14, is the first
that gives the owner something to choose: how many pictures a second the live view is worth.
0.30.6, the same day, is the second: how often the numbers on it are worked out. 0.30.7 makes
twice a second the answer nobody has to give.
The measurements behind all of them are F-78 and F-79 in `ROADMAP.md`.
Their public entry is 1.0.0's.

### 0.30.7

- **The readout is refreshed twice a second unless somebody says otherwise.** The maintainer's
  choice after 0.30.6's figures: twice a second took 5.1 points of a core off a P3 and 4.5 off
  a P1, and once a second only 0.3 and 0.7 more, for markers that trail twice as long. "If
  people want it faster, they can tune it up": every frame and 5 times a second are still in
  the list.
- This changes the picture for whoever upgrades without touching the setting, which is meant:
  a settings file that never named `readout_refresh` gets twice a second. One that says `0`
  keeps every frame, and one edited to something not offered now falls back to twice a second.
- Timelapse clips are untouched, as before: each frame's readout is drawn.

### 0.30.6

- **The readout can be held between refreshes.** A new "Readout refresh" setting in the Readout
  panel: every frame, which is what the plugin always did and stays the default until it has
  been looked at on the printer, or 5 times, twice or once a second. Held, the ruler and the
  markers are drawn once into small patches and pasted onto the pictures in between: the
  numbers stand still, and the hottest and coldest crosses follow what they point at by up to
  one refresh. The colours follow every picture as before. `readout_refresh` in JSON and in the
  settings file, 0 for every frame; it survives a restart.
- It is counted in pictures, as the live view's own pace is, and rounded down so the readout
  is never refreshed less often than asked: at 25 FPS, twice a second is every twelfth picture.
  A refresh cannot come oftener than the pictures do.
- **A change shows at once.** A spot placed, a marker switched off, a unit changed: any change
  of settings starts from an empty hold, so the next picture carries a fresh readout. So does
  coming back from a silence, and so does a picture of another size.
- **Clips are not affected.** Only the live view holds a readout; a timelapse clip draws every
  frame's, whatever this says.
- The pasted readout is the drawn one to within 1 of 255 on the soft edges of the text, which
  is what the bench found. With the setting at every frame no patch is made and the picture is
  byte for byte what it was.
- **Expected, not measured.** The bench on the U1 gave 3.01 ms a frame drawn and 0.59 ms
  pasted (F-78). The plugin's own build of the patches, timed only on a development machine
  and so worth nothing as a figure, costs about two and a half ordinary draws, because each
  glyph is composited onto an empty canvas instead of pasted. If that ratio holds on the
  printer, at 25 FPS the readout goes from about 7.5% of a core to about 2% at once a second,
  3% at twice and 5% at five times. To be measured with
  `scripts/measure-cpu-on-printer.sh U1 30` at each setting.
- **Measured on the U1, 2026-11-14**, in Chrome, one tile open at 25 FPS, 30 s each, % of one
  core at every frame, 5 times, twice and once a second. P3: 54.5, 51.6, 49.4, 49.1. P1: 38.9,
  36.4, 34.4, 33.7. So 2.9, 5.1 and 5.4 points less for a P3 and 2.5, 4.5 and 5.2 for a P1,
  against the 2.5, 4.5 and 5 expected above: the build does cost about two and a half draws on
  the printer too, and once a second buys almost nothing over twice. Seen as well: the numbers
  hold, the markers move once a second at that setting, a spot appears at once, the ruler
  disappears at once, and the timelapse works. In 0.30.6 the default is every frame; 0.30.7
  makes it twice a second.
- **The live view's list reads in FPS** (the maintainer's wording, after 0.30.5 on the U1):
  "25 FPS (Max)", "20 FPS" down to "5 FPS", and "Off", under the title "Live view frame rate".
- Inside: `encode_jpeg` is now two steps a renderer can put a readout between,
  `enlarged_picture` and `picture_jpeg` (0.30.5's `placeholder_jpeg`, renamed for what it has
  become).

### 0.30.5

- **The live view has a rate, and an Off.** A new "Live view frame rate" setting in the Camera
  panel: 25 pictures a second, which is every frame the camera sends and what the plugin always
  did, or 20, 15, 10 or 5, or Off. The list reads "25 FPS (Max)", "20 FPS" and so on down to
  "Off"; it first read "Live view" and "20 a second", reworded on 2026-11-14 after the list was
  used on the U1. Making pictures is nearly all of what the plugin costs while a page
  is showing one (55.0% of a core for a P3 on 0.30.4 against 2.5% with nothing watching), so
  this is the one setting that scales the whole of it. `live_view_rate` in JSON and in the
  settings file; it survives a restart.
- **Off keeps the timelapse.** The camera stays connected and every frame is still read and
  offered to the timelapse; nothing is drawn. Everything that shows the camera shows a "Live
  view off" picture of its own, "The timelapse still records. Turn the live view on in the
  settings", the snapshot included, and the temperatures under the pointer are refused as they
  are for "Stream off". It is for a dashboard left open by somebody who wants the clips and not
  the tile. Stopping the camera is still the deeper switch: that releases the device, and takes
  the timelapse with it.
- The camera sends 25 frames a second and cannot be asked for fewer, so a slower live view draws
  some frames and passes over the rest, counted and not timed. 20, 15 and 10 do not divide 25
  and come out uneven by one camera frame: 10 a second is gaps of 80 and 120 ms in turn. Five is
  the slowest offered because a picture older than a second counts as stale to everything that
  serves one.
- **Measured on the U1, 2026-11-14**, one tile open, 30 s each, % of one core at 25, 20, 15,
  10, 5 and Off. P1: 40.4, 33.8, 25.9, 17.9, 10.2, 2.6. P3: 56.3, 45.5, 34.9, 23.9, 13.3, 2.8.
  The cost follows the pictures, about 1.5 points for each picture a second on a P1 and 2.1 on
  a P3, and 25 reads as 0.30.4 did (41.4 and 55.0). Seen as well: the picture is right at 10
  and 5, the timelapse records with the live view off, the snapshot shows the off screen, and
  switching back on is clean. One thing looked wrong and was the browser: in Safari the Fluidd
  tile stayed black with the live view off, for both cameras, where the settings page showed
  the off screen. In Chrome the tile shows the off screen at once (the maintainer, the same
  day), and the tree did the same in Chromium with a stand-in camera. Why Safari does not draw
  it in the tile is not looked into.
- Two things a slower rate would have changed are kept as they were. Noise reduction averages a
  picture with the camera frame just before it, which is read anyway, and not with the last
  picture drawn, which at 5 a second would be a ghost a fifth of a second behind a moving
  nozzle. And the range eases as far in a second at every rate, where it would have taken five
  times as long at 5. At 25 the renderer gets the settings untouched, so the picture there is
  what 0.30.4 drew.
- The viewer's watchdog reads the "Live view off" picture as it does "Stream off", still on
  purpose: `/health` says `live_view`, and the page neither reopens the stream nor offers to
  record it.
- 757 tests, 56 of them new, in `test_live_view.py`.
- **Not measured on the printer at the time of writing.** What each rate costs is expected to
  follow the number of pictures, about 44, 34, 23 and 13% of a core for a P3 at 20, 15, 10 and
  5 and the idle 2.5% at Off, and those are sums until the running service has been measured.

### 0.30.4

- **A frame comes off the camera in two USB reads instead of fourteen**, or instead of six for a
  P1. The plugin now reads the frame itself, a whole frame at a time, through the device the
  vendored driver opened. Finding the camera, starting the stream, the gain and the shutter are
  still the driver's, and its file is not touched: the reader is the plugin's own code beside
  it, `frame_reader.py`. The picture and the temperatures are the same; the tests read the same
  bytes through the vendored driver and through the new reader and compare them.
- What it is worth, from a bench of the read alone on the U1, in processor time a frame: a P3
  went from 3.51 ms to 0.88 ms and a P1 from 1.85 ms to 0.69 ms, which is 6.6 and 2.9 points of
  one core, paid whether anybody is watching or not. The number of reads was most of it, about
  0.16 ms each; the rest is two copies of the whole frame, and an infrared picture the driver
  built for every frame and the plugin threw away.
- **The driver's reader is kept as the fallback.** A camera the new reader cannot get in step
  with ten times in a row is read the old way until it is next reconnected, and the log says so
  once. Both cameras were counted on the printer sending a frame the way the reader expects.
- Two scripts for the printer came with it: `scripts/measure-threads-on-printer.sh`, which
  splits what the service costs by thread and into user and kernel time, and
  `scripts/bench-read-on-printer.sh`, which times six ways of reading a frame. The second opens
  the camera, so the stream has to be switched off first, and it refuses to run otherwise.
- The tests' stand-in camera now plays its script through a stand-in USB endpoint, in packets,
  so every capture test runs the new reader, and its geometry is held against the vendored
  driver's.
- Measured on the running service, the U1, 2026-11-13, against 0.30.3, as a share of one core.
  P3: nothing watching 9.6% to 2.5%, the capture thread's waits 350.2 a second to 50.0; one tile
  open, smooth enlarging, 62.3% to 55.0% with "Sharpen edges" ticked and 55.0% to 48.2% without.
  P1: nothing watching 2.1% (no 0.30.3 figure; 4.6% on 0.23.0 in September); one tile open, the
  same day and settings for both builds, 44.0% to 41.4% ticked and 39.1% to 36.3% without. The
  bench predicted 6.6 points for a P3 and 2.9 for a P1. Calibration with the stream open carried
  on for both cameras, and the fallback to the driver's reader never engaged (0 log lines).
- The attributions now say what is used from the vendored driver and what the plugin implements
  itself beside it, the frame read and the calibration shutter, instead of "the USB protocol
  layer is not reimplemented here": the manifest, `ATTRIBUTIONS.md`, `VENDORING.md` and the root
  README. The package was rebuilt for it; nothing that runs on the printer changed.
- `NOTICE` rewritten on 2026-11-14, with no change to the package: it still carried the
  plugin's first name, a mistyped pin for the vendored driver, and the three libraries under
  `files/vendor/`, where they have not been since they became wheels.
- `scripts/bench-readout-candidates.py` gained the readout drawn once and pasted, for the ruler,
  the markers and both together, and what a frame costs with the readout refreshed once or five
  times a second. A bench only: nothing in the plugin uses it yet.

### 0.30.3

- **The plugin asks the C library, as it starts, to keep the memory it frees.** 0.30.2's smaller
  picture had glibc handing a P3's whole-frame arrays back and fetching them again on every
  frame. In the profiler on the U1, with glibc told the same thing through its environment, the
  pipeline is 10.08 ms against 13.33 ms and a frame 19.94 ms against 22.68 ms. It says so in the
  log when it has asked, does nothing where the C library is not glibc, and holds a few megabytes
  more than it did.
- The profiling scripts ask too, when the plugin they are measuring does, so that they time a
  process set up like the service.
- Measured on the U1, with a P3 turned a quarter and one tile open: 62.3% of one core enlarged
  smoothly and 64.9% sharply, and 55.0% and 56.0% with "Sharpen edges" unticked. 0.30.0 was
  81.9%, and a P1 is 41.9%. The pipeline is 10.07 ms in the profiler.
- Found on the U1: sharp enlarging now costs a P3 more than smooth, so "Sharp, cheaper" is wrong
  for it; and a P3's first picture is slow to appear in the Fluidd tile, with either enlarging.
  Neither is looked into yet.

### 0.30.2

- **The picture is turned before it is coloured**, not after. The same picture, byte for byte, in
  every rotation and mirroring, and what is copied through the rotated view is one byte a pixel
  instead of three: for a P3 turned a quarter, 3.67 ms for the two steps against 1.70 ms on the
  U1. An upright camera gains nothing, since it was never turned.
- The profiler times turning the indices and turning the coloured picture as two lines, so it
  says which one the plugin under test is paying for.
- Measured on the U1, with a P3 turned a quarter and one tile open: 68.6% of one core against
  0.30.0's 81.9%, and 59.2% with "Sharpen edges" unticked. A frame is 22.68 ms in the profiler
  against 30.63 ms. How Enlarging was set for the two process figures was not noted, and it was
  found on Sharp later that evening; 0.30.3's figures say which is which.
- Found on the U1: about a third of the saving was lost on the way. With the picture smaller,
  the C library stopped keeping the memory of a P3's whole-frame arrays, and gave it back and
  fetched it again on every frame: the pipeline read 13.33 ms where 0.30.0's read 11.56 ms, and
  10.08 ms once the allocator was told to keep what it frees. Not fixed in this build; F-79.

### 0.30.1

- **A P3 is enlarged one and a quarter times instead of twice** when enlarging is smooth and some
  part of the readout is on. That is as far as the text needs, 320x240 instead of 512x384, and the
  doubled encode was 12.46 ms of a 29.98 ms frame on the U1. Sharp enlarging still doubles it, so
  its squares stay even, and a P1 is doubled either way, as before.
- **"Sharpen edges" is a switch**, in the Image panel, ticked unless you untick it. The unsharp
  mask is the largest step in making the picture: 3.83 ms of a P3 frame and 1.70 ms of a P1's.
  Clips follow it.
- The profiling scripts pass the sensor's size and the rotation to the pipeline candidates too,
  which ran at a P1's size whatever was asked for; the profiler reads the enlargement off a frame
  the plugin rendered; and turning the palette indices before the colour lookup is a new candidate.

## Test builds for 0.29.1, on the feature/auto-sensitivity branch

Built on 2026-09-30 and 2026-10-02 and tried on the Ender 2 Pro and the U1. Its public entry is
0.29.1's.

### 0.29.0

- **The timelapse switches to wide range by itself** when something in view passes 145 C, or a
  temperature of your own, for the rest of the print, with five seconds of no frames while the
  camera recalibrates.
- Found on the Ender 2 Pro: the note under the Timelapse panel kept saying the camera would
  switch after the box was unticked, until the page was reloaded, and an Apply applied every
  panel. Fixed in 0.29.1.
- Measured on the U1: high sensitivity reads up to 207.2 C, not 150, and agrees with wide range
  within about 2 C below that, while wide range reads the room 5 to 10 C low. 0.29.1 switches at
  195 C instead of 145.

## Test builds for 0.28.6, on the test/color-bar branch

Built on 2026-09-29 and 2026-09-30 and tried on the U1, to choose how temperatures become colours.
Their public entry is 0.28.6's.

### 0.28.5

- **Four colour scales**, chosen from the seven below: the stretch, a knee at 85%, and the gentle
  and strong logs, over a followed range and a held one, live and in clips. The ruler spans the
  colours on every scale.
- **Clips have their own readout**, with a button to copy the live one, and the colour scale in
  their corner and name on request. "Make the clip again with the current colours".
- **The explanations are behind an (i)** beside each option, and the version is at the foot of the
  settings page.
- Found on the U1: on a phone a second tap did not close an explanation, and the Timelapse panel
  ran off the screen. Fixed in 0.28.6.

### 0.28.4

- **No ten minute wait on a print without the printer's own timelapse.** On a U1, the plugin
  asks during the print whether the firmware is recording one, and only waits for it when it is.
  With the timelapse unticked, the thermal clip is made as soon as the print ends.

### 0.28.3

- **The curved scales cost less.** The curve is worked out for a table of about a thousand
  temperatures instead of for every pixel of every frame.

### 0.28.2

- **The scale is in the clip's name**, on the Timelapse page and in a download, so the clips of one
  print made with different scales can be told apart.
- **Making a clip again no longer waits for the printer's own clip** when it did not come the
  first time: it goes back under the name the first one was given.
- **Delete and Make the clip again are not offered** while a print is waiting to be made into a
  clip or being made into one.

### 0.28.1

- **A test build, to choose how temperatures become colours.** A new "Scale (testing)" setting in
  the Range panel offers seven ways of spreading the palette: today's, today's with the ruler over
  the colours, a straight line over the whole scene, two log curves, and a knee at 85% and 75%.
  They apply while the colours follow the scene, and in timelapse clips made with the print's own
  range.
- **Make the clip again.** Each print that still has its temperatures can be made into a clip
  again, with whatever is chosen now, and each clip is marked with the scale it was made with.
- This entry is replaced once a scale has been chosen.


## 0.26.1

- **The viewer's picture comes back sooner after the printer drops off the network.** While the
  printer was not answering, the viewer asked less and less often, up to every ten seconds, so the
  picture could stay frozen that long after the printer was back. It now keeps asking every two
  seconds, which costs the printer nothing while it cannot be reached.
- **"Printer not answering since" gives the right time.** It showed when the viewer gave up waiting
  for an answer, about four seconds late; it now shows when the unanswered question was asked.

## 0.26.0

- **The viewer notices when its picture stops, and brings it back.** A dropped network connection
  used to leave the viewer showing its last frame for good, looking live, until the page was
  reloaded. The viewer now checks its own picture every two seconds while it is on screen, and if
  the picture has stopped changing for three seconds while the camera is still sending frames, it
  reconnects by itself.
- **When it cannot fix it, it says so.** A line across the top of the picture says what is wrong
  and since when: the printer not answering, the camera not sending frames, or a reconnection
  still under way. It goes away by itself once the picture moves again. With the camera switched
  off, nothing is shown, since the picture is meant to be still.
- **Saved pictures and recordings carry that line**, so a clip can never pass off a frozen stretch
  as a still scene.
- **It comes back sooner after a network drop.** When the laptop's network returns, the viewer
  asks the printer at once instead of waiting for its next scheduled check.
- **Recording waits for the camera.** The Rec button is greyed out while the camera is switched off,
  and after pressing Start until the camera's picture is actually showing. A recording started over
  the "Stream off" picture used to keep that picture's size for the whole clip and squash the
  camera into it.
- **Switching the camera off ends a recording and saves it**, from the viewer or from anywhere
  else. A picture that has merely stopped does not: the recording carries on through the outage,
  with the line showing what happened.
- A new `/thermal/health` says how many frames the plugin has sent and how long ago the last one
  was, which is what the viewer asks.

## 0.25.2

- **The plugin's log says when each thing happened.** Every line now starts with the time in UTC,
  to the millisecond, and the seconds since the printer started, followed by
  `bespok3d/thermal-master` so it is clear which plugin wrote it. Until now a line had no time on
  it at all, so an error in the log could not be matched to anything that happened on the printer.
- **The log also says when the camera comes back.** It used to record only that capture had
  failed, so an outage had a start and no end. A line now marks each time the camera is connected
  and streaming again.

## 0.25.1

- **The camera no longer floods your network when it is open in two places at once.** With the
  picture showing in two tabs, or on a laptop and a phone together, the plugin could send each of
  them the same frame over and over, as fast as the network would carry it. It now sends each new
  frame once to everybody watching. With the camera open in one place only, nothing changes.
- The documentation explains a freeze of about three seconds every thirty or so, which comes from a
  printer stuck reconnecting to a wifi network rather than from the plugin, and what to do about it.
- The documentation says where to buy a camera, with a discount code, through Thermal Master, who
  support this project.

## 0.25.0

- **The plugin says what it is costing the printer.** The settings page carries a line reading its
  own share of the processor straight from the kernel, updated every few seconds while the page is
  open, so the question no longer needs ssh and a shell script.
- Two numbers, because they answer different things: what it is using now, which changes when you
  open a tile or switch the camera off, and what it has averaged since the service started, which
  is the fair figure for a plugin that sleeps most of the day.
- The same numbers are in `/thermal/stats` for anything that would rather draw its own.
- The documentation shipped with the plugin now carries the current cost figures rather than the
  ones measured three phases ago.

The printer has four cores, so 100% of one core is a quarter of the machine, and the figure can
pass 100% because the plugin has more than one thread.

## 0.24.0

- **You can switch the camera off.** A Stop button on the settings page and in the camera view's
  toolbar releases the USB device completely: nothing is read, nothing is rendered, and the camera
  can be unplugged. Press Start to bring it back.
- While it is off, everything that shows the camera shows a "Stream off" picture telling you which
  button brings it back, rather than a broken image or an error. The temperatures behind the
  picture stop being offered, because there are none behind a picture of words.
- The switch survives a restart, so a printer that reboots overnight comes back the way you left
  it.

This is the deeper half of what 0.23.0 started. Idling stops the rendering and keeps the camera in
step so that waking costs one frame; off gives the camera back to the printer entirely.

## 0.23.0

- **The plugin stops working when nobody is watching.** A minute after the last request for a
  picture it stops rendering, and anything asking for one brings it straight back. On the
  maintainer's printer it was using 40.6% of a core with nothing open at all, which was the same
  work it does for somebody actually watching.
- It keeps reading from the camera while it idles, so the device stays in step and waking costs one
  frame rather than a reconnect. A request waits briefly for a fresh frame rather than being
  answered with whatever was on screen an hour ago.
- Coming back from an idle starts the picture clean, because averaging a new frame with a minute
  old one shows as a ghost and the auto-range would ease away from a scene that has moved on.

Nothing to switch on, and nothing changes while you have the camera open: a dashboard tile holds
the stream open, which is what keeps it awake.

## 0.22.1

- **"Hold what I see now" looked like it did nothing, and the next Apply undid it.** The plugin was
  holding the range correctly; the settings page just went on showing "Follow the scene" and the
  old numbers, and pressing Apply then posted what it was showing. The page now takes the state the
  plugin sends back after every change, so a button that changes something is visible in the
  controls straight away.

## 0.22.0

- **You can hold the display range.** Until now the colours always followed the scene, which is why
  the picture re-maps and the ruler jumps when a toolhead crosses the view. The settings page now
  offers two fixed temperatures instead, and a "Hold what I see now" button that fills them in from
  the picture in front of you.
- A held range is what lets you see a bed. Hold 90 to 100 on a bed at 100 C and its own variation
  gets the whole palette, instead of arriving as one flat colour because the room is also in frame.
- The ruler follows: on a held range it spans the two temperatures you set and stays put, and a
  triangle at either end means the scene has gone past it. On auto it is unchanged.
- Holding costs the printer slightly less than following the scene, because there is nothing to
  measure.

Your measurements are unaffected either way: every number comes from the sensor rather than from
the picture.

## 0.21.0

- **A choice about how the picture is enlarged.** The plugin makes the picture bigger before it
  draws the readout on it, so the text has room, and until now it always blended the sensor's
  pixels while doing so. "Enlarging" on the settings page now offers sharp instead, which leaves
  the pixels as squares and costs the printer about a sixth less work per frame. Smooth stays the
  default, so nothing changes unless you change it, and it only applies while some part of the
  readout is switched on.
- Closing a browser tab no longer writes an error into the plugin's log. It never was one.

## 0.20.0

A quarter off the work the plugin does before it even starts encoding, and the picture is unchanged
byte for byte. Three steps, all measured on a printer rather than guessed at:

- The display range is read from one pass over the frame instead of two.
- The colour lookup goes through a faster path in numpy for exactly the same result.
- Averaging a frame with the one before it stays in whole numbers rather than going out to floating
  point and back.

Nothing to change and nothing to notice, unless you were watching the processor.

## 0.19.0

- **Spots.** Click the picture in Spot mode and it marks that place and reads its temperature, up to
  four at once, for watching several parts of a print at the same time. Click a spot to remove it,
  or Clear to remove them all.
- They belong to the printer rather than to the browser, so they are burned into the picture like
  the rest of the readout: the dashboard tile shows them, a recorded clip shows them, and a second
  browser shows the same ones.
- Each spot reads a 3 by 3 patch averaged rather than a single pixel, which is steadier and easier
  to land on.
- Rotating or mirroring the picture clears the spots, because they name places on a picture that
  just moved. Better to place them again than to have them point confidently at the wrong thing.
- **A saved image and a recorded clip now carry the region box's average**, not just the box. The
  page still lists the hottest, coldest and average beside the picture, where there is room.
- Changing a setting no longer makes the picture re-settle for a second, which matters when placing
  four spots one after another.

## 0.18.0

- **Record a clip.** A Rec button in the viewer writes what the camera is showing to a video file,
  with the readout and any region box on it, at the picture's own resolution rather than at
  whatever size the window happens to be. Press it again to stop and the file is saved.
- MP4 where the browser will produce one, which is Safari and recent Chrome, and WebM where it will
  not. The file is named for what it actually is, so a clip that says `.mp4` is an MP4.
- A recording stops itself after ten minutes, and leaving the page saves what it has rather than
  losing it. It is held in memory until it stops, which is why there is a limit at all.
- The controls are centred under the picture instead of pressed against the left edge.

Recording needs the page open. A tile hides the button, because recording is something you set up
deliberately and a tile has no room for a button you press twice a year.

## 0.17.0

- **The controls are in the tile now.** They used to appear only above a certain height, which no
  dashboard tile reaches, so the buttons were unreachable without opening the viewer full screen.
  They sit under the picture in one compact row, and grow when the page is opened properly.
- **The picture is bigger in a tile, not smaller, despite the extra row.** The readout took half the
  width when it sat beside the picture and three lines when it sat underneath; it now takes a narrow
  column and a single line. In a wide tile the picture is the same size it was with no controls at
  all, and in a narrow one it is about a fifth larger than it was in 0.16.0.
- **The settings page has a way back to the camera.** A tile is an iframe with no browser chrome, so
  following the settings link stranded you there until you reloaded the dashboard.
- The mode button is labelled "Box" rather than "Measure", which is what lets the whole row fit
  across a narrow tile, and the zoom percentage hides on a tile too narrow to hold it.

## 0.16.0

- **One camera instead of two.** The dashboard listed `Thermal` and `Thermal live`, two views of the
  same camera, and a camera that comes from a config file is read-only in Fluidd, so there was no way
  in the UI to hide either of them. The interactive viewer is now the camera, under the name you
  chose, with no word added to it.
- The plain picture was registered beside it as a fallback: a script can break and an image cannot.
  That is now the page's own job. With JavaScript switched off, the viewer's stylesheet fits the
  picture into the tile by itself, and the script only ever overrides that layout. The stream and
  the still are still served at `/thermal/stream.mjpg` and `/thermal/snapshot.jpg` for anything that
  wants a plain picture.
- The viewer no longer hides the picture while it is working out the camera's shape. It shows it
  letterboxed until it knows, then takes over.

After updating, the old `<name> live` entry disappears on its own. If you had picked one of the two
in Fluidd's dashboard settings, check that the remaining one is shown.

## 0.15.0

- **The viewer showed nothing until you moved the pointer over it.** The picture is positioned by
  the page's own script, and nothing called that on load, so a viewer opened with no region saved
  was simply blank. It only ever looked fine because a saved region, or a mouse passing over,
  happened to trigger a redraw.
- **The readout goes beside the picture when there is width going spare**, which in a Fluidd tile
  there always is: the tile is landscape and a rotated camera is portrait, so putting the numbers
  underneath spent the one dimension the picture needed. The picture is now about twice the size in
  a tile.
- The plugin tells the page the picture's shape when it serves it, so the layout is right from the
  first moment rather than after the stream decodes.
- The moving line inside the ruler is gone. It marked where the auto-ranging stops, which the
  gradient's own edge already shows.
- The triangles are back, at the ends of the ruler, in the colours of the markers they belong to.

## 0.14.0

Three fixes, all found by looking at it on a real printer.

- **The live tile showed controls and no camera.** Laid out for a window, the controls wrapped to
  three rows and took the whole tile, leaving the picture 41 pixels tall. The picture now keeps most
  of the tile and the toolbar appears only when the page is opened properly, which is when you want
  it anyway.
- **The ruler now spans the coldest and hottest in view**, so its ends are the same numbers the
  markers show. It used to be labelled with the auto-ranged bounds, which is a different and more
  defensible thing, and which read as a contradiction to everyone who looked at it: a ruler topped
  25.3 next to a marker reading 30.0. The auto-ranged part is now shown by two ticks, and the flat
  bands above and below them are the truth about where colour stops carrying information.
- The ruler's end labels are coloured to match the markers they name.

Worth knowing about the two tiles: they are separate. The plain one is a picture and always will be;
pointing and measuring happen on the live one.

## 0.13.0

Tools on the viewer.

- Zoom with the wheel or the buttons, up to eight times, towards whatever you are pointing at. Fit
  puts it back.
- A Pan mode for dragging the picture around when zoomed in, since Measure mode uses the same
  gesture for the region box and a phone has no key to hold down.
- A units button. It changes the unit for the whole plugin, not just this page, so the burned-in
  readout on the plain tile says the same thing.
- Save image writes a PNG at the sensor's own resolution with the region drawn on it.

`/thermal/settings` now also accepts JSON, which is how the units button changes one setting without
disturbing the others. A posted form cannot express that: an unticked checkbox and an absent one look
identical, so a form carrying only a unit would read as every part of the readout switched off.

## 0.12.0

- Drag a box on the viewer and it reports the hottest, coldest and average temperature inside it,
  live. Click to clear it, or press Escape.
- The box is remembered, so a tile that reloads comes back measuring the same thing.
- While a box exists the viewer keeps reading frames even with the pointer away, which is the point
  of drawing one: you set it and then go and do something else.

The box is measured in the browser from the frame it already has, so dragging it costs the printer
nothing and the numbers change as fast as you move it.

## 0.11.0

An interactive viewer, as a second camera tile.

- Point anywhere on the picture and read the temperature of that pixel.
- The tile appears in Fluidd and Mainsail next to the plain one, named after your camera with
  "live" on the end. The plain tile stays exactly as it was: if the viewer ever breaks, you still
  have a camera.
- Nothing is fetched until you point at something, and it stops a few seconds after you stop, so a
  tile nobody is using costs the printer the stream and not one byte more.

The page reads temperatures itself rather than asking the printer one question at a time. A new
endpoint hands it every pixel at once, with the emissivity correction already applied, which is why
hovering is instant and why the browser never has to carry a second copy of the physics.

## 0.10.0

- The coldest pixel is now marked too, in blue, with its own tick on the ruler. A cold corner of a
  bed is as much a fault as a hot nozzle.
- Every part of the readout is its own switch: the ruler, the centre crosshair, the hottest pixel
  and the coldest pixel, in any combination.
- Labels no longer write over each other. With three markers, two of them landing close together is
  the normal case rather than the unlucky one, so each label now takes the first position that is
  clear of the ones already drawn.
- The ruler ticks whichever extremes you are marking, so the two always agree.

Settings files from earlier versions are carried through both renames, so whatever you had switched
off stays off.

## 0.9.0

- The temperature ruler and the on-image readings are now separate switches. One switch could not
  say "ruler, but no numbers over the picture", or the reverse, and both are reasonable things to
  want.
- With the ruler off, the readings use the full width instead of avoiding the column it used to sit
  in.
- Turning both off returns the plugin to exactly the cost it had before there was any readout.

A settings file from an earlier version carries the single old switch, and whichever way it was set
is applied to both, so a readout you had deliberately turned off stays off.

## 0.8.5

Internal only. Nothing about the plugin behaves differently.

- The streamer was one 1,786 line script that nothing could import and no type checker could read.
  It is now a package of nine modules behind the same entry point, and mypy runs on all of it.

## 0.8.4

- Emissivity offers 0.98, which is about right for skin, water and matte paint.

## 0.8.3

- Calibrate now works again. It was broken by 0.8.2: the button posted under a field named "action",
  which in a browser shadows the form's own action property, so the page fetched a nonsense URL,
  fell back to an ordinary submit, and an ordinary submit does not carry the button that was
  pressed. The page reloaded and reported that nothing had been asked for, which was true.
- Every URL the page uses is now relative, so the page works both behind the printer's web server
  and on a direct connection to the plugin's port. The form previously only worked behind the
  former, which is also what made the fallback above land on a 404.

## 0.8.2

- Changing a setting no longer throws you out to the printer's home page. The control page redirected
  to an absolute path after every change, and since nginx publishes the page under a prefix that the
  plugin never sees, that path resolved to the Fluidd dashboard rather than back to the page.
- Changing a setting no longer interrupts the video. The page now applies settings in the background
  where the browser allows it, so the picture is not torn down and reopened every time. Without
  JavaScript it still posts and reloads as before, and lands back at the controls rather than at the
  top of the page.
- `/thermal/settings` now also reports what the camera is doing, in the same words the page uses.

## 0.8.1

- Calibration works on the P1. The vendored driver's own shutter helper reads back the frame that
  follows using offsets measured on a P3, which on a P1 point past the end of the frame buffer, so
  it failed with "memoryview assignment: lvalue and rvalue have different structures" and no
  calibration happened. The command is now sent directly and the mistimed frame is left to the
  ordinary reader, which resynchronises by itself.
- The colorbar now shows where the hottest pixel falls on it, with a triangle at the top when it is
  above the range entirely. The bar is labelled with the range the palette covers, which is a
  percentile of the scene rather than its extremes, so the hotspot marker legitimately reads higher
  than the top of the bar and the two looked like they were contradicting each other.
- A marker's label is kept clear of the colorbar's own labels, including when the marker itself is
  in that corner. The previous attempt only avoided the bar, which is much narrower than its labels.

## 0.8.0

Camera controls, from the same page.

- **Gain.** High sensitivity, which covers -20 to 150 C, or wide range, which covers 0 to 550 C at
  lower sensitivity. High is the default. The choice is re-sent automatically if the camera is
  unplugged and replugged, since a camera that has just been opened is in its own default.
- **Calibrate now.** Closes the camera's internal shutter for a moment and re-levels the sensor
  against it. The camera does this by itself about every ninety seconds; the button is for when the
  picture has drifted and you would rather not wait. It costs one frame.
- **Emissivity.** How much of what a surface radiates is its own heat rather than a reflection of
  the room. A shiny surface reads cold until you tell the plugin it is shiny. Presets from 1.00
  down to 0.10, and any value in between if you edit the settings file.

Note that the default emissivity is 0.95, which suits matte plastic, so readings are slightly
higher than in 0.7.x where no correction was applied at all. Set it to 1.00 for the old numbers.

Emissivity changes the numbers only, never the picture. Gain does change the picture, because it
changes what the sensor is measuring.

## 0.7.1

- A marker sitting near the right edge no longer labels itself on top of the colorbar's own label.
  Its number goes on its left instead. Seen on hardware with the hotspot in the bottom corner.

## 0.7.0

A temperature readout, drawn into the picture.

- A colorbar down the right edge, labelled at both ends with the range currently mapped to the
  palette.
- A crosshair in the centre with the temperature under it, and a marker on the hottest pixel in
  view with its temperature.
- Celsius or Fahrenheit, from the control page.
- The readout can be switched off, which returns the plugin to exactly what 0.6.0 cost.
- `/thermal/stats` serves the same numbers as JSON, plus the frame average and the coldest pixel,
  for anything that would rather draw its own.

The readout is drawn into the frame rather than onto the control page, because the surface that
matters is the camera tile in Fluidd and Mainsail, and that is a plain image with nowhere to hang
an annotation. Switching it on doubles the encoded size so the text stays legible, which is the
only part of it that costs anything measurable.

Temperatures are the camera's own uncorrected readings. Emissivity is not applied yet, so treat
them as a good guide to a scene and not as measurements.

## 0.6.0

A control page, at `/thermal/` on the printer.

- Palette, rotation and mirroring can be changed while the camera is running. No reinstall, no
  editing files, and the choice survives a restart.
- Orientation is now applied by the plugin rather than by the browser. Rotating in Fluidd needed a
  setting that could only be changed by reinstalling, and the installer did not reliably carry the
  choice through, so a sideways camera could not be corrected at all. Fluidd's own rotation stays at
  zero to avoid rotating twice.
- Rotation and mirroring are no longer install-time questions, since they are live settings now.

Note that changing a setting costs about a second: the auto-ranging restarts and has to find the
scene again.

## 0.5.3

- The tile shape is now an install setting. Rotating by 90 or 270 turns a 4:3 image into a 3:4 one,
  and the tile stayed 4:3, so the picture sat inside it with black bars at the sides and shrank to
  fit. Set the tile shape to match the rotation and it fills again.

## 0.5.2

- Much lighter on the printer. The stream was being upscaled four times and encoded at 640x480,
  twenty-five times a second, so the browser could scale it back down to fit the tile. Measured at
  62.5% of a CPU core, of which the resize and the oversized encode were about four fifths. Frames
  are now encoded at the sensor's own size, with the quality raised to compensate, and the browser
  does the scaling it was going to do anyway. Expect a large drop in CPU with no visible difference.

## 0.5.1

- Rotation and mirroring are now install settings. A camera registered from a config file is
  read-only in Fluidd's settings panel, which shows "Managed by your Moonraker configuration" and
  greys the controls out, so there was no way to correct a camera that is mounted sideways. Both are
  applied by the browser, so they cost the printer nothing.

## 0.5.0

The picture, rather than the plumbing.

- The image no longer flickers. The displayed temperature range was recalculated from scratch every
  frame, so the whole picture re-scaled whenever anything warm entered or left the view. The range
  now eases towards the scene over about a second instead of snapping to it.
- Six palettes: ironbow, rainbow, white hot, black hot, military, sepia. Ironbow stays the default.
  Switching still means changing the service argument; live switching comes with the control page.
- Temporal noise reduction. Each frame is averaged with the one before, which halves the per-pixel
  sensor noise that made still scenes look like they were simmering.
- Detail enhancement. A mild unsharp mask, so a part reads as a part rather than a warm blob.
- Faster. The display range is measured on raw sensor counts instead of converting the whole frame
  to Celsius first, which is the same answer for less work.

## 0.4.1

- Fixed: the camera showed nothing in Safari, while working in Chrome. Fluidd renders this kind of
  camera by fetching the stream inside a Web Worker, and that fetch fails in Safari even though the
  same URL opens fine as a page. The camera is now registered as an adaptive stream, which polls the
  snapshot on a timer rather than holding a connection open. Slightly less smooth, and it works in
  every browser instead of most of them.

## 0.4.0

- The P3 (256x192) works too. The plugin probes the USB bus at startup and drives whichever camera it
  finds, so there is nothing to configure and nothing to pick wrongly. Both models speak the same
  protocol and differ only in sensor size.
- With no camera attached the service now says so plainly instead of assuming a P1 and failing
  somewhere deeper in the driver.

## 0.3.0

Robustness, all of it in how the capture loop handles a camera that misbehaves. No new features.

- Stopping the service now puts the camera down properly. There was no signal handler, so the
  service was killed outright and the USB interface was left claimed with its alternate setting
  still set, which could stop the next start from opening the camera at all. If you ever had to
  unplug the camera to get the stream back after a restart, this was why.
- A single frame whose markers disagree no longer tears down the camera and reconnects. That cost
  at least six seconds of dead video for a fault that clears on the very next read.
- A camera that stops producing frames is now noticed. Previously an empty read was treated exactly
  like a slow frame, so the stream froze on its last good image, forever, while the service went on
  reporting itself healthy.
- Reconnect attempts back off, three seconds doubling to a minute, instead of retrying every three
  seconds. A camera that is simply not plugged in no longer writes a log line every three seconds
  until somebody notices. The backoff resets as soon as a session produces frames again.

## 0.2.3

- Log lines said `thermal-p1`, the plugin's old name. Everything on the printer logs to one place, so
  a line labelled with a name that no longer exists sends whoever is reading it after the wrong
  plugin. Nothing else changed: 0.2.2 and 0.2.3 behave identically.

## 0.2.2

- Fixed: the service never started, so the camera tile could only ever show an error. It was launched
  as plain `python3`, which is the system interpreter, not the one in the virtual environment the
  daemon provisions for this plugin. numpy happened to be importable there and pyusb was not, so it
  died on `No module named 'usb'` every time it was restarted. It now runs
  `$PLUGIN_VENV/bin/python3`, as the platform's own reference Python plugin does.

## 0.2.1

- Fixed: the camera tile showed frames but was labelled an error. Snapshot requests carry a
  cache-busting parameter, and the streamer matched the raw request path, so every one of them got a
  404 while the stream itself kept working. The query string is now discarded before routing, which
  also fixes the `?action=stream` form that mjpg-streamer clients use.

## 0.2.0

Renamed from `thermal-p1`. The old name could only ever describe one of the two cameras this is meant
to serve, and nothing usable had shipped under it, so this is a clean break rather than a migration.

- The camera registers itself in Fluidd and Mainsail. Previously the plugin's own documentation walked
  you through adding it by hand under Settings.
- You choose the name it appears under when you install it.
- The stream is served through a reverse proxy the plugin ships, so `/thermal/stream.mjpg` and
  `/thermal/snapshot.jpg` resolve on the printer's own address.
- The streamer runs as a service the daemon supervises, replacing a hand-written init script.
- numpy, Pillow and pyusb are installed into a virtual environment belonging to this plugin alone,
  instead of being unpacked by hand next to the code. Nothing is installed into the interpreter
  Klipper and Moonraker run on.
- The vendored USB driver is pinned to an exact upstream commit and verified by checksum, instead of
  being fetched from whatever the upstream branch held at build time.
- The udev rule is gone. The service runs as root, so the camera node needs no permission change, and
  the streamer reconnects on its own when the camera is replugged.

Not yet verified on hardware. See the device trial checklist in the plugin's README.

## 0.1.0

First cut, as `thermal-p1`. Never verified against a camera.
