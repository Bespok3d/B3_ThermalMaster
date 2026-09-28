# Changelog

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
